import { mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
const STORES=new Set(['kb_sources','kb_videos','kb_meta']);
const MAX_RECORD_BYTES=25*1024*1024;
export class DesktopStore{
  constructor(dataDir){
    this.dataDir=resolve(dataDir);mkdirSync(this.dataDir,{recursive:true,mode:0o700});
    this.db=new DatabaseSync(join(this.dataDir,'knowledge.sqlite'),{timeout:5000});
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS mirror_records(store TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(store,key));
      CREATE TABLE IF NOT EXISTS mirror_meta(id INTEGER PRIMARY KEY CHECK(id=1),generation TEXT NOT NULL,record_count INTEGER NOT NULL,completed_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS mirror_stage(upload_id TEXT NOT NULL,store TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(upload_id,store,key));
      CREATE TABLE IF NOT EXISTS canonical_records(store TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(store,key));
      CREATE TABLE IF NOT EXISTS canonical_meta(id INTEGER PRIMARY KEY CHECK(id=1),generation TEXT NOT NULL,record_count INTEGER NOT NULL,completed_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS canonical_stage(upload_id TEXT NOT NULL,store TEXT NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(upload_id,store,key));`);
    this.#migrateLegacyMirror();
  }
  #migrateLegacyMirror(){
    const current=this.db.prepare('SELECT generation FROM canonical_meta WHERE id=1').get();if(current)return;
    const legacy=this.db.prepare('SELECT generation,record_count,completed_at FROM mirror_meta WHERE id=1').get();if(!legacy)return;
    this.db.exec('BEGIN');try{this.db.exec("INSERT OR REPLACE INTO canonical_records(store,key,payload,updated_at) SELECT store,key,payload,updated_at FROM mirror_records");this.db.prepare('INSERT OR REPLACE INTO canonical_meta(id,generation,record_count,completed_at) VALUES(1,?,?,?)').run(String(legacy.generation),Number(legacy.record_count)||0,String(legacy.completed_at));this.db.exec('COMMIT')}catch(e){this.db.exec('ROLLBACK');throw e}
  }
  info(){const row=this.db.prepare('SELECT generation,record_count,completed_at FROM canonical_meta WHERE id=1').get();return{configured:true,mode:'canonical-sqlite',snapshot:row||null}}
  begin(uploadId){this.db.exec('DELETE FROM canonical_stage');return{uploadId}}
  put(uploadId,store,records){if(!STORES.has(store))throw new Error('Invalid desktop canonical store.');if(!Array.isArray(records)||records.length>100)throw new Error('Desktop canonical batch exceeds 100 records.');const stmt=this.db.prepare('INSERT OR REPLACE INTO canonical_stage(upload_id,store,key,payload) VALUES(?,?,?,?)');this.db.exec('BEGIN');try{for(const r of records){const key=String(r?.key||'').slice(0,500);const payload=JSON.stringify(r?.value);if(!key||Buffer.byteLength(payload)>MAX_RECORD_BYTES)throw new Error('Invalid desktop canonical record.');stmt.run(uploadId,store,key,payload)}this.db.exec('COMMIT')}catch(e){this.db.exec('ROLLBACK');throw e}}
  commit(uploadId,generation){const count=Number(this.db.prepare('SELECT COUNT(*) n FROM canonical_stage WHERE upload_id=?').get(uploadId)?.n||0);this.db.exec('BEGIN');try{this.db.exec('DELETE FROM canonical_records');this.db.prepare("INSERT INTO canonical_records(store,key,payload,updated_at) SELECT store,key,payload,datetime('now') FROM canonical_stage WHERE upload_id=?").run(uploadId);this.db.prepare('INSERT OR REPLACE INTO canonical_meta(id,generation,record_count,completed_at) VALUES(1,?,?,?)').run(String(generation),count,new Date().toISOString());this.db.prepare('DELETE FROM canonical_stage WHERE upload_id=?').run(uploadId);this.db.exec('COMMIT');return{generation:String(generation),recordCount:count}}catch(e){this.db.exec('ROLLBACK');throw e}}
  page(store,afterKey='',limit=50){if(!STORES.has(store))throw new Error('Invalid desktop canonical store.');const after=String(afterKey||'').slice(0,500),take=Math.max(1,Math.min(100,Number(limit)||50));const rows=this.db.prepare('SELECT key,payload FROM canonical_records WHERE store=? AND key>? ORDER BY key LIMIT ?').all(store,after,take);const records=rows.map(r=>({key:r.key,value:JSON.parse(r.payload)}));return{store,records,nextKey:records.length===take?records.at(-1).key:null}}
  records(){return [...STORES].flatMap(store=>{const out=[];let after='';for(;;){const page=this.page(store,after,100);out.push(...page.records.map(r=>({store,...r})));if(!page.nextKey)break;after=page.nextKey}return out})}
}
export function desktopEnabled(){return process.env.DESKTOP_MODE==='1'}
