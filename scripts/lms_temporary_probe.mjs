// Calls the actual installed upstream registration with a synthetic API client.
// No upstream entrypoint, auth client, session, browser or real network request.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import {Readable} from 'node:stream';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {boundedDownloadClient} from '../backend/services/lms_runtime/bounded_download.mjs';

const [packageRoot,source,destination,mode,headerMode]=process.argv.slice(2);
globalThis.fetch=async()=>{throw Error('Real networking prohibited in synthetic probe');};
for (const name of ['log','info','warn','error','debug','trace']) console[name]=()=>{};
const manifest=JSON.parse(await fsp.readFile(path.join(packageRoot,'package.json'),'utf8'));
if (manifest.name!=='brightspace-mcp-server' || manifest.version!=='3.13.18') throw Error('Unreviewed package');
const {registerDownloadFile}=await import(pathToFileURL(path.join(packageRoot,'build/tools/download-file.js')));
const {D2LApiClient}=await import(pathToFileURL(path.join(packageRoot,'build/api/index.js')));
let handler,reads=0,cancelled=false;
const size=(await fsp.stat(source)).size;
const api=Object.assign(Object.create(D2LApiClient.prototype),{
  le(course,endpoint) {
    if(course!==501 || endpoint!=='/content/topics/7/file') throw Error('Unexpected synthetic resource');
    return 'synthetic-course-file';
  },
  async resolvePath(resource) {return resource;},
  async withAuthentication(resource,operation) {return operation({accessToken:'synthetic-token'});},
  async makeRawRequest(resource) {
    if(resource!=='synthetic-course-file') throw Error('Unexpected synthetic API call');
    reads++;
    const iterator=fs.createReadStream(source,{highWaterMark:64*1024})[Symbol.asyncIterator]();
    const body=new ReadableStream({
      async pull(controller) {
        const {done,value}=await iterator.next();
        if(done)controller.close();else controller.enqueue(value);
      },
      async cancel() {cancelled=true;await iterator.return();},
    },{highWaterMark:0});
    const headers={'Content-Type':'application/octet-stream','Content-Disposition':`attachment; filename="${path.basename(source)}"`};
    if(headerMode!=='missing')headers['Content-Length']=String(headerMode==='understated'?1:size);
    return new Response(body,{headers});
  },
});
const preload=globalThis[Symbol.for('AIStudyAgent.boundedDownloads')]===true;
if(mode==='stdio') {
  if(!preload)throw Error('Bounded startup required');
  const require=createRequire(path.join(packageRoot,'package.json'));
  const {McpServer}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/server/mcp.js')));
  const {StdioServerTransport}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/server/stdio.js')));
  const server=new McpServer({name:'synthetic-brightspace',version:'3.13.18'});
  registerDownloadFile(server,api);
  await server.connect(new StdioServerTransport());
} else {
registerDownloadFile({registerTool(name,configuration,callback) {
  if(name!=='download_file' || handler)throw Error('Unexpected registration');
  handler=callback;
}}, mode==='bounded_disk'&&!preload?boundedDownloadClient(api):api);
const args={courseId:501,topicId:7};
if(mode!=='inline')Object.assign(args,{downloadPath:path.resolve(destination),customFilename:'material'});
const result=await handler(args);
// Only synthetic fixture content is returned to the parent; never print it to user.
process.stdout.write(JSON.stringify({result,reads,cancelled,preload}));
}
