#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const indexPath = path.join(projectRoot, 'apps', 'web', 'vue3', 'index.html');
const html = fs.readFileSync(indexPath, 'utf8');

function assert(condition, message) {
  if (!condition) {
    console.error(`FAIL: ${message}`);
    process.exitCode = 1;
  }
}

function countLiteral(needle) {
  return html.split(needle).length - 1;
}

const requiredDialogBindings = [
  'confirmParams.fgseaComparison',
  'confirmParams.fgseaMinSize',
  'confirmParams.fgseaMaxSize',
  'confirmParams.fgseaNperm',
  'confirmParams.fgseaSeed',
  'confirmParams.wgcnaMinMean',
  'confirmParams.wgcnaMaxGenes',
  'confirmParams.wgcnaMinModuleSize',
  'confirmParams.wgcnaMergeCutHeight',
  'confirmParams.wgcnaPowers',
  'confirmParams.candidateMaxDegProteins',
  'confirmParams.candidateRunInterproscan',
  'confirmParams.annotationApplications',
  'confirmParams.enrichBackgroundSource',
  'confirmParams.enrichSpecies',
  'confirmParams.enrichKeyType',
  'confirmParams.keggOrganism',
  'confirmParams.topPathways',
];

for (const binding of requiredDialogBindings) {
  assert(
    countLiteral(binding) >= 2,
    `${binding} should appear in both parameter confirmation dialog blocks`
  );
}

const requiredExecutionAssignments = [
  'requestBody.fgseaComparison',
  'requestBody.fgseaMinSize',
  'requestBody.fgseaMaxSize',
  'requestBody.fgseaNperm',
  'requestBody.fgseaSeed',
  'requestBody.wgcnaMinMean',
  'requestBody.wgcnaMaxGenes',
  'requestBody.wgcnaMinModuleSize',
  'requestBody.wgcnaMergeCutHeight',
  'requestBody.wgcnaPowers',
  'requestBody.candidateMaxDegProteins',
  'requestBody.candidateRunInterproscan',
  'requestBody.annotationApplications',
  'requestBody.enrichBackgroundSource',
  'requestBody.enrichSpecies',
  'requestBody.enrichKeyType',
  'requestBody.keggOrganism',
  'requestBody.topPathways',
];

for (const assignment of requiredExecutionAssignments) {
  assert(
    html.includes(assignment),
    `${assignment} should be assigned in applyRnaseqExecutionParams`
  );
}

assert(html.includes('topPathways: 20'), 'confirmParams should define topPathways default');
assert(html.includes('featureCounts raw fragment counts'), 'dialog should state DESeq2 uses featureCounts raw fragment counts');
assert(html.includes('Salmon/tximport TPM'), 'dialog should state expression plots use Salmon/tximport TPM after bridge QC');
assert(html.includes('InterProScan'), 'candidate dialog should warn about InterProScan requirements');

if (!process.exitCode) {
  console.log('RNA-seq confirmation parameter coverage ok');
}
