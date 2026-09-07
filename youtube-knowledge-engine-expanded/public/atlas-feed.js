const MARKET_RULES=[
  ['crypto',/\b(?:crypto|bitcoin|btc|ethereum|eth|solana|sol)\b/i],
  ['forex',/\b(?:forex|fx|currency|currencies|eur\/?usd|gbp\/?usd|usd\/?jpy|aud\/?usd|xau\/?usd|gold)\b/i],
  ['stock',/\b(?:stock|stocks|equity|equities|etf|shares?|nasdaq|s&p|spy|qqq|tqqq)\b/i],
  ['futures',/\b(?:futures?|e-mini|emini|micro futures?|\bmes\b|\bmnq\b|\bnq\b|\bes\b|\bcl\b|\bgc\b)\b/i],
];
const TF_RE=/\b(\d{1,3})\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days|w|week|weeks)\b/gi;
const NAMED_TF_RE=/\b(daily|weekly|monthly|intraday|scalp|scalping|swing)\b/gi;
const TAG_RULES=[
  ['breakout',/\bbreakout|donchian|channel break/i],
  ['pullback',/\bpullback|retracement|buy the dip|dip buy/i],
  ['trend',/\btrend|moving average|\bema\b|\bsma\b/i],
  ['momentum',/\bmomentum|rate of change|\broc\b/i],
  ['mean_reversion',/mean.?reversion|reversal|oversold|overbought/i],
  ['volatility',/volatility|\batr\b|bollinger/i],
  ['rsi',/\brsi\b/i],
  ['macd',/\bmacd\b/i],
  ['vwap',/\bvwap\b/i],
  ['volume',/\bvolume|obv|money flow/i],
  ['support_resistance',/support|resistance/i],
  ['price_action',/price action|candlestick|wick rejection/i],
  ['session',/london session|new york session|asian session|opening range|\borb\b/i],
  ['risk',/stop.?loss|risk.?reward|position siz/i],
];

function clean(value,limit=1600){return String(value||'').replace(/\s+/g,' ').trim().slice(0,limit)}
function isoDate(value){const t=Date.parse(String(value||''));return Number.isFinite(t)?new Date(t).toISOString().slice(0,10):''}
function uniq(values,limit=16){const out=[];for(const raw of values||[]){const v=clean(raw,80);if(v&&!out.some(x=>x.toLowerCase()===v.toLowerCase()))out.push(v);if(out.length>=limit)break}return out}
function timestampUrl(videoId,startMs=0){return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}${startMs?`&t=${Math.floor(Number(startMs)/1000)}s`:''}`}
function stableIdeaId(sourceId,videoId,startMs){const bucket=Math.floor(Math.max(0,Number(startMs)||0)/15000);return `yti:${clean(sourceId,80)}:${clean(videoId,32)}:${bucket}`}
function detectMarkets(text){const out=[];for(const [name,re] of MARKET_RULES)if(re.test(text))out.push(name);return out}
function detectTimeframes(text){const out=[];for(const m of text.matchAll(TF_RE)){const n=m[1],u=m[2].toLowerCase();const unit=u[0]==='m'?'m':u[0]==='h'?'h':u[0]==='d'?'d':'w';out.push(`${n}${unit}`)}for(const m of text.matchAll(NAMED_TF_RE))out.push(m[1].toLowerCase());return uniq(out,10)}
function detectTags(text){return TAG_RULES.filter(([,re])=>re.test(text)).map(([name])=>name).slice(0,12)}
function quality(passage){const completeness=Math.max(0,Math.min(6,Number(passage?.completeness)||0));const score=Math.max(0,Math.min(5,Number(passage?.score)||0));return Math.round((.72*(completeness/6)+.28*(score/5))*1000)/1000}

export function passageToAtlasIdea(report,passage,{minimumCompleteness=2}={}){
  if(!report?.source||!passage?.videoId)return null;
  const completeness=Number(passage.completeness)||0;
  if(completeness<minimumCompleteness)return null;
  const text=clean(passage.text,1800);
  if(!text)return null;
  const published=isoDate(passage.published);
  if(!published)return null;
  const context=`${passage.title||''} ${text}`;
  const featureTags=Object.entries(passage.features||{}).filter(([,v])=>Boolean(v)).map(([k])=>k);
  return{
    idea_id:stableIdeaId(report.source.id,passage.videoId,passage.startMs),
    video_id:clean(passage.videoId,32),
    channel_id:clean(report.source.id,120),
    channel_title:clean(report.source.title,180),
    published_at:published,
    title:clean(passage.title||'YouTube strategy passage',240),
    summary:text,
    strategy_rules:[text],
    markets:detectMarkets(context),
    timeframes:detectTimeframes(context),
    tags:uniq([...detectTags(context),...featureTags],16),
    source_kind:'youtube_intelligence_strategy_passage',
    specification_quality:quality(passage),
    claimed_metrics:{},
    evidence:{
      url:timestampUrl(passage.videoId,passage.startMs),
      start_ms:Math.max(0,Number(passage.startMs)||0),
      completeness,
      features:{...(passage.features||{})},
      channel_url:clean(report.source.url,500),
      source_synced_at:clean(report.source.syncedAt,64),
      analysis_generated_at:clean(report.generatedAt,64),
    },
  };
}

export function analysisReportToAtlasFeed(report,{minimumCompleteness=2,maxIdeas=120}={}){
  const rows=[];
  const seen=new Set();
  for(const passage of report?.strategyPassages||[]){
    const idea=passageToAtlasIdea(report,passage,{minimumCompleteness});
    if(!idea||seen.has(idea.idea_id))continue;
    seen.add(idea.idea_id);rows.push(idea);
  }
  rows.sort((a,b)=>
    (Number(b.specification_quality)||0)-(Number(a.specification_quality)||0)||
    String(a.published_at).localeCompare(String(b.published_at))||
    String(a.idea_id).localeCompare(String(b.idea_id))
  );
  return rows.slice(0,Math.max(1,Math.min(500,Number(maxIdeas)||120)));
}

export function mergeAtlasFeeds(existing,incoming,{maxIdeas=5000}={}){
  const map=new Map();
  for(const row of [...(existing||[]),...(incoming||[])]){
    if(!row?.idea_id)continue;
    const prior=map.get(row.idea_id);
    if(!prior||Number(row.specification_quality||0)>=Number(prior.specification_quality||0))map.set(row.idea_id,row);
  }
  return [...map.values()].sort((a,b)=>
    String(a.published_at||'').localeCompare(String(b.published_at||''))||
    String(a.idea_id).localeCompare(String(b.idea_id))
  ).slice(-Math.max(1,Math.min(50000,Number(maxIdeas)||5000)));
}

export function titleLooksStrategyRelevant(title=''){
  const s=String(title||'');
  return /\b(?:strategy|setup|indicator|backtest|tested|entry|exit|stop.?loss|take.?profit|breakout|scalp|scalping|swing|day trad|forex|futures|stock|crypto|bitcoin|rsi|macd|vwap|moving average|bollinger|ichimoku|supertrend|donchian|volume|support|resistance|price action|orb|opening range)\b/i.test(s);
}
