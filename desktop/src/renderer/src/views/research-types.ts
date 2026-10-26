export type ResearchProject = { workspaceId: string; path: string; title: string; sessionIds: string[] };
export type ResearchEvidence = {
  id: string;
  kind: 'question' | 'paper' | 'note' | 'result';
  title: string;
  detail: string;
  url?: string;
  doi?: string;
  pmid?: string;
  file?: string;
};
export type ResearchNode = {
  id: string;
  kind: 'question' | 'paper' | 'method' | 'finding';
  label: string;
  summary: string;
  basis: 'source' | 'hypothesis';
  evidenceIds: string[];
};
export type ResearchEdge = { id: string; source: string; target: string; label: string; basis: 'source' | 'hypothesis'; evidenceIds: string[] };
export type ResearchSuggestion = { id: string; title: string; reason: string; requirements: string[]; steps: string[]; evidenceIds: string[]; limitations: string };
export type ResearchAnalysis = { title: string; summary: string; nodes: ResearchNode[]; edges: ResearchEdge[]; suggestions: ResearchSuggestion[] };
export type ResearchState = {
  version: 1;
  revision: number;
  topic: string;
  evidence: ResearchEvidence[];
  analysis: ResearchAnalysis | null;
  analysisStale: boolean;
  updatedAt: string;
};
export type ResearchTab = 'graph' | 'suggestions';
