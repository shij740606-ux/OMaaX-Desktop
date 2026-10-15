const assert=require('node:assert/strict');
const {safeName}=require('./relay.cjs');
for(const name of ['../raw','/etc/passwd','a/../../x','a\\b','C:/x','a//b','a/./b','']) assert.throws(()=>safeName(name));
assert.equal(safeName('reads/sample.fastq.gz'),'reads/sample.fastq.gz');
console.log('Transfer path boundary checks passed');
