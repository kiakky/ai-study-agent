// Retain upstream index.js and authentication. Bound raw file responses only.
// No installed-package edits, session access or browser work in this module.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {boundedDownloadClient} from './bounded_download.mjs';

const root=process.env.AI_STUDY_MCP_PACKAGE_ROOT;
if (!root || !path.isAbsolute(root)) throw Error('Pinned MCP root required');
const manifest=JSON.parse(await fs.readFile(new URL('./reviewed-build.json',import.meta.url),'utf8'));
const pkg=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'));
if(pkg.name!=='brightspace-mcp-server' || pkg.version!=='3.13.18') throw Error('Unreviewed MCP package');
for(const relative of ['build/api/client.js','build/tools/download-file.js','build/utils/download-helpers.js','build/utils/download-errors.js']) {
  const file=path.join(root,relative);
  if((await fs.lstat(file)).isSymbolicLink()) throw Error('Unreviewed MCP source');
  const bytes=await fs.readFile(file);
  if(createHash('sha256').update(bytes).digest('hex')!==manifest[relative]) throw Error('Unreviewed MCP source');
}
const {D2LApiClient}=await import(pathToFileURL(path.join(root,'build/api/index.js')));
const {DownloadError}=await import(pathToFileURL(path.join(root,'build/utils/download-errors.js')));
const original=D2LApiClient.prototype.getRaw;
D2LApiClient.prototype.getRaw=function(...args) {
  return boundedDownloadClient({getRaw:()=>original.apply(this,args)},undefined,
    ()=>new DownloadError('tooLarge')).getRaw();
};
globalThis[Symbol.for('AIStudyAgent.boundedDownloads')]=true;
