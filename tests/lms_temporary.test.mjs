import {test} from 'node:test';
import assert from 'node:assert/strict';
import {boundedDownloadClient,MAX_DOWNLOAD_BYTES} from '../backend/services/lms_runtime/bounded_download.mjs';

function fixture(chunks,header) {
  let cancelled=false,rawCalls=0;
  const api={marker:1,le(){return this.marker;},async getRaw(){
    rawCalls++;
    let offset=0;
    const body=new ReadableStream({pull(c){if(offset===chunks.length)c.close();else c.enqueue(chunks[offset++]);},cancel(){cancelled=true;}},{highWaterMark:0});
    return new Response(body,{headers:header===undefined?{}:{'Content-Length':header}});
  }};
  return {api,state:()=>({cancelled,rawCalls})};
}

test('rejects arbitrary or unbounded download limits',()=>{
  for(const size of [0,-1,Infinity,NaN,1.5,MAX_DOWNLOAD_BYTES+1])assert.throws(()=>boundedDownloadClient({},size));
});
test('preserves API method receiver and does not mutate the client',()=>{
  const f=fixture([]);const wrapped=boundedDownloadClient(f.api,10);
  assert.equal(wrapped.le(),1);assert.notEqual(wrapped.getRaw,f.api.getRaw);
});
test('accepts body exactly at byte ceiling',async()=>{
  const f=fixture([new Uint8Array(3),new Uint8Array(7)],'10');
  const r=await boundedDownloadClient(f.api,10).getRaw('synthetic');
  assert.equal((await r.arrayBuffer()).byteLength,10);assert.equal(f.state().rawCalls,1);
});
test('rejects declared oversize before consuming body and cancels source',async()=>{
  const f=fixture([new Uint8Array(11)],'11');
  await assert.rejects(boundedDownloadClient(f.api,10).getRaw('synthetic'));
  assert.equal(f.state().cancelled,true);
});
for(const header of [undefined,'1','invalid'])test(`counts actual bytes for ${String(header)} Content-Length`,async()=>{
  const f=fixture([new Uint8Array(6),new Uint8Array(6)],header);
  const r=await boundedDownloadClient(f.api,10).getRaw('synthetic');
  await assert.rejects(r.arrayBuffer());assert.equal(f.state().cancelled,true);
});
test('source errors fail instead of returning a complete body',async()=>{
  const api={async getRaw(){return new Response(new ReadableStream({pull(c){c.error(Error('synthetic-stream-failure'));}}));}};
  const r=await boundedDownloadClient(api,10).getRaw();await assert.rejects(r.arrayBuffer());
});
test('downstream cancellation reaches original stream',async()=>{
  const f=fixture([new Uint8Array(6)]);const r=await boundedDownloadClient(f.api,10).getRaw();
  await r.body.cancel();assert.equal(f.state().cancelled,true);
});
