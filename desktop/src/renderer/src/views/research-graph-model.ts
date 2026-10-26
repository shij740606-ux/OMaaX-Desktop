// Rendering and routing share the same card geometry, without loading ELK on the UI thread.
export const RESEARCH_NODE_WIDTH=224;
export const RESEARCH_NODE_HEIGHT=128;
export type ResearchGraphLayout={positions:Record<string,{x:number;y:number}>;paths:Record<string,string>};
