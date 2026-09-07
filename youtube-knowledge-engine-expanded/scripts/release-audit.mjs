import { auditReleaseTree } from '../lib/release-audit.js';

const result = await auditReleaseTree(new URL('../', import.meta.url));
if (!result.ok) {
  console.error('Release audit failed:');
  for (const v of result.violations) console.error(`- ${v.code}: ${v.path}${v.variable ? ` (${v.variable})` : ''}`);
  process.exit(1);
}
console.log(`Release audit passed (${result.totalBytes} bytes, no secret/runtime artifacts detected).`);
