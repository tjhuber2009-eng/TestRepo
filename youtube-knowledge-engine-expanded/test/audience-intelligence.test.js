import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeAudienceComment,analyzeAudienceSamples} from '../public/audience-core.js';


test('audience comment normalization strips identity while retaining research fields',()=>{
  const c=normalizeAudienceComment({id:'c1',text:'  Please explain this again  ',like_count:12,author:'Real Person',author_is_uploader:true},{id:'AAAAAAAAAAA',title:'Example'});
  assert.deepEqual(c,{commentId:'c1',videoId:'AAAAAAAAAAA',videoTitle:'Example',text:'Please explain this again',likeCount:12,timestamp:null,parent:'',authorIsUploader:true});
  assert.equal('author' in c,false);
});

test('audience intelligence extracts topics, questions, requests and pain points',()=>{
  const report=analyzeAudienceSamples([{videoId:'AAAAAAAAAAA',title:'Risk Video',comments:[
    {id:'1',text:'Can you make a video explaining position sizing and risk?',like_count:30},
    {id:'2',text:'Position sizing is confusing and this example does not work for me.',like_count:20},
    {id:'3',text:'Great explanation of position sizing and risk, thanks!',like_count:10},
    {id:'4',text:'Please cover risk after a losing streak next.',like_count:8},
  ]}]);
  assert.equal(report.summary.videosSampled,1);
  assert.equal(report.summary.comments,4);
  assert.ok(report.topTopics.some(x=>x.topic==='risk'&&x.count>=2));
  assert.ok(report.questions.some(x=>x.commentId==='1'));
  assert.ok(report.requests.length>=2);
  assert.ok(report.painPoints.some(x=>x.commentId==='2'));
  assert.match(report.methodology,/not representative surveys/);
});
