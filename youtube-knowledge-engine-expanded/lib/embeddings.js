import { YoutubeError } from './youtube.js';
import { envInt, normalizeSpaces, readResponseTextLimited, sha256 } from './util.js';

export function embeddingsConfigured(){
  return Boolean(process.env.EMBEDDING_API_URL && process.env.EMBEDDING_MODEL && (process.env.EMBEDDING_AUTH==='none' || process.env.EMBEDDING_API_KEY));
}

export function embeddingProviderInfo(){
  const url=String(process.env.EMBEDDING_API_URL||'').trim();
  const model=String(process.env.EMBEDDING_MODEL||'').trim();
  const dimensions=envInt('EMBEDDING_DIMENSIONS',0,0,8192);
  return {configured:embeddingsConfigured(),model,providerId:url&&model?sha256(`${url}|${model}|${dimensions||'native'}`).slice(0,24):'',requestedDimensions:dimensions||null};
}

export function normalizeVector(vector){
  if(!Array.isArray(vector)||!vector.length)throw new YoutubeError('Embedding provider returned an invalid vector.','INVALID_EMBEDDING_VECTOR',502);
  const maxDim=envInt('EMBEDDING_MAX_DIMENSIONS',8192,64,32768);
  if(vector.length>maxDim)throw new YoutubeError(`Embedding vector exceeds the ${maxDim}-dimension safety limit.`,'EMBEDDING_DIMENSIONS_TOO_LARGE',502);
  let sum=0;const out=new Array(vector.length);
  for(let i=0;i<vector.length;i++){const n=Number(vector[i]);if(!Number.isFinite(n))throw new YoutubeError('Embedding provider returned a non-finite vector value.','INVALID_EMBEDDING_VECTOR',502);out[i]=n;sum+=n*n}
  const norm=Math.sqrt(sum);if(!Number.isFinite(norm)||norm<=0)throw new YoutubeError('Embedding provider returned a zero-length vector.','INVALID_EMBEDDING_VECTOR',502);
  for(let i=0;i<out.length;i++)out[i]/=norm;
  return out;
}

export function parseEmbeddingPayload(data,expected){
  const rows=Array.isArray(data?.data)?data.data:Array.isArray(data?.embeddings)?data.embeddings:null;
  if(!rows||rows.length!==expected)throw new YoutubeError('Embedding provider returned an unexpected number of vectors.','INVALID_EMBEDDING_RESPONSE',502);
  const ordered=Array.isArray(data?.data)?[...rows].sort((a,b)=>(Number(a?.index)||0)-(Number(b?.index)||0)).map(x=>x?.embedding):rows.map(x=>Array.isArray(x)?x:x?.embedding);
  const vectors=ordered.map(normalizeVector);const dimensions=vectors[0]?.length||0;
  if(!dimensions||vectors.some(v=>v.length!==dimensions))throw new YoutubeError('Embedding provider returned vectors with inconsistent dimensions.','INVALID_EMBEDDING_RESPONSE',502);
  return {vectors,dimensions};
}

export async function embedTexts(input){
  if(!embeddingsConfigured())throw new YoutubeError('Semantic embeddings are not configured. Lexical search remains available.','EMBEDDINGS_NOT_CONFIGURED',424);
  if(!Array.isArray(input)||!input.length)throw new YoutubeError('At least one text is required for embeddings.','INVALID_EMBEDDING_INPUT',400);
  const maxBatch=envInt('EMBEDDING_MAX_BATCH',32,1,128);if(input.length>maxBatch)throw new YoutubeError(`Embedding requests are limited to ${maxBatch} texts per batch.`,'EMBEDDING_BATCH_TOO_LARGE',400);
  const maxChars=envInt('EMBEDDING_MAX_INPUT_CHARS',8000,500,50000);
  const texts=input.map(x=>normalizeSpaces(x).slice(0,maxChars));if(texts.some(x=>!x))throw new YoutubeError('Embedding texts must not be empty.','INVALID_EMBEDDING_INPUT',400);
  const info=embeddingProviderInfo();const body={model:info.model,input:texts};if(info.requestedDimensions)body.dimensions=info.requestedDimensions;
  const headers={'content-type':'application/json'};const auth=String(process.env.EMBEDDING_AUTH||'bearer').toLowerCase();if(auth==='bearer')headers.authorization=`Bearer ${process.env.EMBEDDING_API_KEY||''}`;else if(auth==='x-api-key')headers['x-api-key']=process.env.EMBEDDING_API_KEY||'';else if(auth!=='none')throw new YoutubeError('Unsupported EMBEDDING_AUTH mode.','INVALID_EMBEDDING_CONFIG',500);
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),envInt('EMBEDDING_TIMEOUT_MS',120000,10000,600000));
  try{
    const r=await fetch(process.env.EMBEDDING_API_URL,{method:'POST',headers,body:JSON.stringify(body),signal:controller.signal});
    const raw=await readResponseTextLimited(r,envInt('EMBEDDING_MAX_RESPONSE_MB',16,1,128)*1024*1024);let data;try{data=JSON.parse(raw)}catch{data={}}
    if(!r.ok)throw new YoutubeError(data?.error?.message||`Embedding provider returned HTTP ${r.status}`,'EMBEDDING_PROVIDER_ERROR',502);
    const parsed=parseEmbeddingPayload(data,texts.length);return {...parsed,model:info.model,providerId:info.providerId};
  }catch(e){
    if(e?.name==='AbortError')throw new YoutubeError('Embedding provider timed out. The request is not retried automatically to avoid duplicate cost.','EMBEDDING_TIMEOUT',504);
    if(e?.code==='RESPONSE_TOO_LARGE')throw new YoutubeError('Embedding provider response exceeded the configured safety limit.','EMBEDDING_RESPONSE_TOO_LARGE',502);
    throw e;
  }finally{clearTimeout(timer)}
}
