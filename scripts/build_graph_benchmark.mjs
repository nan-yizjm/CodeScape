import { build } from 'vite';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
const repository=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const directory=resolve(repository,'output/graph-benchmark',randomUUID());
await mkdir(directory,{recursive:true});
const baseline=process.argv.includes('--baseline');
const baselineSource=baseline?execFileSync('C:/Program Files/Git/cmd/git.exe',['show','2a1c67f:apps/web/src/Canvas.tsx'],{cwd:repository,encoding:'utf8'}):null;
await build({root:resolve(repository,'tests/graph-benchmark'),configFile:false,
  define:{__BASELINE__:JSON.stringify(baseline)},
  plugins:baseline?[{name:'application-owned-baseline',load(id){if(id.replaceAll('\\','/').endsWith('/apps/web/src/Canvas.tsx'))return baselineSource;}}]:[],
  build:{outDir:directory,emptyOutDir:false},logLevel:'warn'});
await writeFile(resolve(repository,'.runtime/graph-benchmark-build.json'),JSON.stringify({directory,baseline}), 'utf8');
console.log('人工图验证资源已生成：'+directory);
