use keyring::Entry;
use rand::{distributions::Alphanumeric,Rng};
use serde::{Deserialize,Serialize};
use std::{fs,net::{TcpListener,TcpStream},path::{PathBuf},sync::Mutex,thread,time::Duration};
use tauri::{AppHandle,Manager,State,WebviewUrl,WebviewWindowBuilder};
use tauri::menu::{Menu,MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_shell::{process::CommandChild,ShellExt};

const KEYRING_SERVICE:&str="com.youtubeintelligence.desktop";

#[derive(Default,Clone,Serialize,Deserialize)]
#[serde(rename_all="camelCase",deny_unknown_fields)]
struct ProviderConfig{
  transcription_url:String,transcription_auth:String,transcription_model:String,
  research_url:String,research_auth:String,research_model:String,
  embedding_url:String,embedding_auth:String,embedding_model:String,
  visual_model:String,visual_processing:String,
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct ProviderStatus{
  config:ProviderConfig,
  transcription_secret_stored:bool,research_secret_stored:bool,embedding_secret_stored:bool,visual_secret_stored:bool,
}

#[derive(Deserialize)]
#[serde(rename_all="camelCase",deny_unknown_fields)]
struct ProviderSettingsInput{
  config:ProviderConfig,
  transcription_secret:Option<String>,research_secret:Option<String>,embedding_secret:Option<String>,visual_secret:Option<String>,
}

struct BackendRuntime{child:Option<CommandChild>,port:u16,token:String}
struct BackendState(Mutex<BackendRuntime>);

fn clean(v:String,max:usize)->String{v.trim().chars().take(max).collect()}
fn provider_url_allowed(value:&str)->bool{
  if value.is_empty(){return true}
  let Ok(url)=tauri::Url::parse(value) else{return false};
  if !url.username().is_empty()||url.password().is_some(){return false}
  if url.scheme()=="https"{return true}
  url.scheme()=="http"&&matches!(url.host_str(),Some("127.0.0.1")|Some("localhost")|Some("::1"))
}
fn normalized_config(mut c:ProviderConfig)->Result<ProviderConfig,String>{
  c.transcription_url=clean(c.transcription_url,2000);c.transcription_auth=clean(c.transcription_auth,40);c.transcription_model=clean(c.transcription_model,200);
  c.research_url=clean(c.research_url,2000);c.research_auth=clean(c.research_auth,40);c.research_model=clean(c.research_model,200);
  c.embedding_url=clean(c.embedding_url,2000);c.embedding_auth=clean(c.embedding_auth,40);c.embedding_model=clean(c.embedding_model,200);
  c.visual_model=clean(c.visual_model,200);c.visual_processing=clean(c.visual_processing,40);
  for url in [&c.transcription_url,&c.research_url,&c.embedding_url]{if !provider_url_allowed(url){return Err("Provider URL must use HTTPS (or exact local loopback HTTP) and must not contain embedded credentials.".into())}}
  for auth in [&c.transcription_auth,&c.research_auth,&c.embedding_auth]{if !auth.is_empty()&&!matches!(auth.as_str(),"bearer"|"x-api-key"|"none"){return Err("Unsupported provider auth mode.".into())}}
  if !c.visual_processing.is_empty()&&!matches!(c.visual_processing.as_str(),"agentic"|"static"){return Err("Unsupported visual processing mode.".into())}
  Ok(c)
}
fn provider_config_path(app:&AppHandle)->Result<PathBuf,String>{let dir=app.path().app_data_dir().map_err(|e|e.to_string())?;fs::create_dir_all(&dir).map_err(|e|e.to_string())?;Ok(dir.join("provider-settings.json"))}
fn load_provider_config(app:&AppHandle)->ProviderConfig{provider_config_path(app).ok().and_then(|p|fs::read_to_string(p).ok()).and_then(|s|serde_json::from_str::<ProviderConfig>(&s).ok()).unwrap_or_default()}
fn save_provider_config(app:&AppHandle,c:&ProviderConfig)->Result<(),String>{let target=provider_config_path(app)?;let temp=target.with_extension("json.tmp");let body=serde_json::to_vec_pretty(c).map_err(|e|e.to_string())?;fs::write(&temp,body).map_err(|e|e.to_string())?;#[cfg(unix)]{use std::os::unix::fs::PermissionsExt;let _=fs::set_permissions(&temp,fs::Permissions::from_mode(0o600));}fs::rename(temp,target).map_err(|e|e.to_string())}
fn secret(name:&str)->Option<String>{Entry::new(KEYRING_SERVICE,name).ok()?.get_password().ok().filter(|s|!s.is_empty())}
fn secret_exists(name:&str)->bool{secret(name).is_some()}
fn update_secret(name:&str,value:Option<String>)->Result<(),String>{let Some(value)=value else{return Ok(())};let entry=Entry::new(KEYRING_SERVICE,name).map_err(|e|format!("OS credential store unavailable: {e}"))?;if value.is_empty(){let _=entry.delete_credential();Ok(())}else if value.len()>16_384{Err("Credential exceeds the 16 KB safety limit.".into())}else{entry.set_password(&value).map_err(|e|format!("Could not save credential in the OS credential store: {e}"))}}
fn provider_status(app:&AppHandle)->ProviderStatus{ProviderStatus{config:load_provider_config(app),transcription_secret_stored:secret_exists("transcription"),research_secret_stored:secret_exists("research"),embedding_secret_stored:secret_exists("embedding"),visual_secret_stored:secret_exists("visual")}}
fn tool_path(app:&AppHandle,name:&str)->Option<PathBuf>{let ext=if cfg!(windows){".exe"}else{""};let p=app.path().resource_dir().ok()?.join("tools").join(format!("{name}{ext}"));p.is_file().then_some(p)}
fn set_cfg_env(mut cmd:tauri_plugin_shell::process::Command,c:&ProviderConfig)->tauri_plugin_shell::process::Command{
  let pairs=[("TRANSCRIPTION_API_URL",&c.transcription_url),("TRANSCRIPTION_AUTH",&c.transcription_auth),("TRANSCRIPTION_MODEL",&c.transcription_model),("RESEARCH_API_URL",&c.research_url),("RESEARCH_AUTH",&c.research_auth),("RESEARCH_MODEL",&c.research_model),("EMBEDDING_API_URL",&c.embedding_url),("EMBEDDING_AUTH",&c.embedding_auth),("EMBEDDING_MODEL",&c.embedding_model),("VISUAL_MODEL",&c.visual_model),("VISUAL_PROCESSING",&c.visual_processing)];for (k,v) in pairs{if !v.is_empty(){cmd=cmd.env(k,v)}}
  for (env_key,secret_key) in [("TRANSCRIPTION_API_KEY","transcription"),("RESEARCH_API_KEY","research"),("EMBEDDING_API_KEY","embedding"),("VISUAL_GEMINI_API_KEY","visual")]{if let Some(v)=secret(secret_key){cmd=cmd.env(env_key,v)}}cmd
}
fn spawn_backend(app:&AppHandle)->Result<(CommandChild,u16,String),String>{let token:String=rand::thread_rng().sample_iter(&Alphanumeric).take(48).map(char::from).collect();let probe=TcpListener::bind("127.0.0.1:0").map_err(|e|e.to_string())?;let port=probe.local_addr().map_err(|e|e.to_string())?.port();drop(probe);let data=app.path().app_data_dir().map_err(|e|e.to_string())?;fs::create_dir_all(&data).map_err(|e|e.to_string())?;let backend=app.path().resource_dir().map_err(|e|e.to_string())?.join("backend").join("server.js");let mut cmd=app.shell().sidecar("yke-node").map_err(|e|e.to_string())?.arg(backend).env("HOST","127.0.0.1").env("PORT",port.to_string()).env("NODE_ENV","production").env("DESKTOP_MODE","1").env("DESKTOP_SESSION_TOKEN",&token).env("DESKTOP_DATA_DIR",data.join("native")).env("MONITOR_DATA_DIR",data.join("monitoring")).env("MONITORING_ENABLED","1").env("AI_CHECKPOINT_DIR",data.join("ai-checkpoints"));cmd=set_cfg_env(cmd,&load_provider_config(app));if let Some(p)=tool_path(app,"yt-dlp"){cmd=cmd.env("YTDLP_BIN",p)}if let Some(p)=tool_path(app,"ffmpeg"){cmd=cmd.env("FFMPEG_BIN",p)}let (_rx,child)=cmd.spawn().map_err(|e|e.to_string())?;for _ in 0..80{if TcpStream::connect(("127.0.0.1",port)).is_ok(){return Ok((child,port,token))}thread::sleep(Duration::from_millis(100))}let _=child.kill();Err("desktop sidecar did not become ready".into())}
fn backend_url(port:u16,token:&str)->Result<tauri::Url,String>{format!("http://127.0.0.1:{port}/?desktopToken={token}").parse().map_err(|e|format!("invalid backend URL: {e}"))}
fn restart_backend(app:&AppHandle,state:&BackendState)->Result<(),String>{let mut lock=state.0.lock().map_err(|_|"backend state lock poisoned".to_string())?;if let Some(child)=lock.child.take(){let _=child.kill();}let (child,port,token)=spawn_backend(app)?;let url=backend_url(port,&token)?;lock.child=Some(child);lock.port=port;lock.token=token;drop(lock);if let Some(w)=app.get_webview_window("main"){w.navigate(url).map_err(|e|e.to_string())?;}Ok(())}

#[tauri::command]
fn desktop_provider_status(app:AppHandle)->ProviderStatus{provider_status(&app)}
#[tauri::command]
fn desktop_save_provider_settings(app:AppHandle,state:State<'_,BackendState>,settings:ProviderSettingsInput)->Result<ProviderStatus,String>{let config=normalized_config(settings.config)?;update_secret("transcription",settings.transcription_secret)?;update_secret("research",settings.research_secret)?;update_secret("embedding",settings.embedding_secret)?;update_secret("visual",settings.visual_secret)?;save_provider_config(&app,&config)?;restart_backend(&app,state.inner())?;Ok(provider_status(&app))}
fn show_settings(app:&AppHandle){if let Some(w)=app.get_webview_window("settings"){let _=w.show();let _=w.set_focus();return;}let _=WebviewWindowBuilder::new(app,"settings",WebviewUrl::App("desktop-settings.html".into())).title("YouTube Intelligence · Provider Settings").inner_size(760.0,780.0).min_inner_size(620.0,600.0).build();}

fn main(){tauri::Builder::default().plugin(tauri_plugin_shell::init()).manage(BackendState(Mutex::new(BackendRuntime{child:None,port:0,token:String::new()}))).invoke_handler(tauri::generate_handler![desktop_provider_status,desktop_save_provider_settings]).setup(|app|{let (child,port,token)=spawn_backend(app.handle()).map_err(|e|->Box<dyn std::error::Error>{e.into()})?;{let state=app.state::<BackendState>();let mut lock=state.0.lock().unwrap();lock.child=Some(child);lock.port=port;lock.token=token.clone();}let url=backend_url(port,&token).map_err(|e|->Box<dyn std::error::Error>{e.into()})?;let win=WebviewWindowBuilder::new(app,"main",WebviewUrl::External(url)).title("YouTube Intelligence Desktop").inner_size(1440.0,900.0).min_inner_size(900.0,650.0).on_navigation(|u|u.scheme()=="http"&&u.host_str()==Some("127.0.0.1")).build()?;let show=MenuItem::with_id(app,"show","Show",true,None::<&str>)?;let settings=MenuItem::with_id(app,"settings","AI provider settings",true,None::<&str>)?;let quit=MenuItem::with_id(app,"quit","Quit",true,None::<&str>)?;let menu=Menu::with_items(app,&[&show,&settings,&quit])?;let mut tray=TrayIconBuilder::new().menu(&menu);if let Some(icon)=app.default_window_icon(){tray=tray.icon(icon.clone());}tray.on_menu_event(move |app,e|match e.id.as_ref(){"show"=>{if let Some(w)=app.get_webview_window("main"){let _=w.show();let _=w.set_focus();}},"settings"=>show_settings(app),"quit"=>app.exit(0),_=>{}}).build(app)?;let w=win.clone();win.on_window_event(move |e|if let tauri::WindowEvent::CloseRequested{api,..}=e{api.prevent_close();let _=w.hide();});Ok(())}).build(tauri::generate_context!()).expect("desktop init failed").run(|app,e|if let tauri::RunEvent::ExitRequested{..}=e{if let Some(child)=app.state::<BackendState>().0.lock().unwrap().child.take(){let _=child.kill();}})}
