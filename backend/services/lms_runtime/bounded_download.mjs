// Wrap the pinned API client's raw responses, retaining its authentication.
export const MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024;

export function boundedDownloadClient(api, maxBytes=MAX_DOWNLOAD_BYTES,
  limitError=()=>Error('Document exceeds private download ceiling')) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes<1 || maxBytes>MAX_DOWNLOAD_BYTES)
    throw Error('Invalid download ceiling');
  return new Proxy(api, {get(target,key) {
    if (key!=='getRaw') {
      const value=Reflect.get(target,key);
      return typeof value==='function' ? value.bind(target) : value;
    }
    return async (...args)=>{
      const response=await target.getRaw(...args);
      const declared=response.headers.get('Content-Length');
      if (declared!==null && /^\d+$/.test(declared) && Number(declared)>maxBytes) {
        await response.body?.cancel();
        throw limitError();
      }
      if (!response.body) return response;
      const reader=response.body.getReader();
      let received=0;
      const body=new ReadableStream({
        async pull(controller) {
          try {
            const {done,value}=await reader.read();
            if (done) {controller.close();return;}
            received+=value.byteLength;
            if (received>maxBytes) {
              await reader.cancel();
              controller.error(limitError());
              return;
            }
            controller.enqueue(value);
          } catch (error) {controller.error(error);}
        },
        cancel(reason) {return reader.cancel(reason);},
      }, {highWaterMark:0});
      return new Response(body,{status:response.status,statusText:response.statusText,headers:response.headers});
    };
  }});
}
