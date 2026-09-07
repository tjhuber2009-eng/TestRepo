import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import process from 'node:process';

const root=resolve(new URL('..',import.meta.url).pathname);
const manifest='SOURCE-SHA256SUMS.txt';
const ignoredDirs=new Set(['.git','node_modules']);
const ignoredRelativeDirs=new Set(['src-tauri/binaries','src-tauri/target']);
const ignoredNames=new Set([manifest]);
const ignoredExt=/\.(?:zip|tgz)$/i;

async function walk(dir){
  const out=[];
  for(const entry of await readdir(dir,{withFileTypes:true})){
    if(entry.isDirectory()&&ignoredDirs.has(entry.name))continue;
    const path=resolve(dir,entry.name),rel=relative(root,path).replaceAll('\\','/');
    if(entry.isDirectory()&&ignoredRelativeDirs.has(rel))continue;
    if(entry.isDirectory())out.push(...await walk(path));
    else if(entry.isFile()&&!ignoredNames.has(entry.name)&&!ignoredExt.test(entry.name))out.push(path);
  }
  return out;
}
function sha(bytes){return createHash('sha256').update(bytes).digest('hex')}
async function expected(){
  const files=(await walk(root)).sort((a,b)=>relative(root,a).localeCompare(relative(root,b)));
  const lines=[];
  for(const file of files)lines.push(`${sha(await readFile(file))}  ${relative(root,file).replaceAll('\\','/')}`);
  return lines.join('\n')+'\n';
}
if(process.argv.includes('--verify')){
  let actual='';try{actual=await readFile(resolve(root,manifest),'utf8')}catch{console.error(`${manifest} is missing.`);process.exit(1)}
  const want=await expected();
  if(actual!==want){console.error(`${manifest} does not match the current source tree. Run npm run manifest and review the diff.`);process.exit(1)}
  console.log(`Verified ${actual.trim().split('\n').filter(Boolean).length} source files.`);
}else{
  const body=await expected();await writeFile(resolve(root,manifest),body);console.log(`Wrote ${manifest} for ${body.trim().split('\n').filter(Boolean).length} source files.`);
}
