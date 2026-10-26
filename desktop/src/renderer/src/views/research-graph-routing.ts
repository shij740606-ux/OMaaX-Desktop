import type {ElkNode} from 'elkjs/lib/elk-api';
import type {ResearchAnalysis} from './research-types';
import {RESEARCH_NODE_WIDTH,RESEARCH_NODE_HEIGHT,type ResearchGraphLayout} from './research-graph-model';

export async function routeResearchGraph(analysis:ResearchAnalysis,engine:{layout:(graph:ElkNode)=>Promise<ElkNode>}):Promise<ResearchGraphLayout>{
 const graph:ElkNode={id:'research',layoutOptions:{
  'elk.algorithm':'layered','elk.direction':'RIGHT','elk.edgeRouting':'ORTHOGONAL',
  'elk.spacing.nodeNode':'50','elk.spacing.edgeNode':'24','elk.spacing.edgeEdge':'14',
  'elk.layered.spacing.nodeNodeBetweenLayers':'110','elk.layered.spacing.edgeNodeBetweenLayers':'26',
  'elk.layered.spacing.edgeEdgeBetweenLayers':'16','elk.layered.mergeEdges':'false',
  'elk.padding':'[top=32,left=32,bottom=32,right=32]','elk.randomSeed':'1',
 },children:analysis.nodes.map(node=>({id:'node/'+node.id,width:RESEARCH_NODE_WIDTH,height:RESEARCH_NODE_HEIGHT,
  layoutOptions:{'elk.portConstraints':'FIXED_POS'},ports:[
   {id:'input/'+node.id,x:0,y:RESEARCH_NODE_HEIGHT/2,width:0,height:0,layoutOptions:{'elk.port.side':'WEST'}},
   {id:'output/'+node.id,x:RESEARCH_NODE_WIDTH,y:RESEARCH_NODE_HEIGHT/2,width:0,height:0,layoutOptions:{'elk.port.side':'EAST'}},
  ]})),edges:analysis.edges.map(edge=>({id:'edge/'+edge.id,sources:['output/'+edge.source],targets:['input/'+edge.target]}))};
 // The supplied engine computes the layout in a worker; conversion here is lightweight.
  const result=await engine.layout(graph);
  const positions=Object.fromEntries((result.children||[]).map(node=>{
   if(!Number.isFinite(node.x)||!Number.isFinite(node.y))throw Error('Missing node position');
   return [node.id.slice(5),{x:node.x!,y:node.y!}];
  }));
  const paths=Object.fromEntries((result.edges||[]).map(edge=>{
   const sections=edge.sections||[];
   if(!sections.length)throw Error('Missing routed relationship');
   return [edge.id.slice(5),sections.map(section=>[section.startPoint,...section.bendPoints||[],section.endPoint].map((point,index)=>`${index?'L':'M'} ${point.x} ${point.y}`).join(' ')).join(' ')];
  }));
  return {positions,paths};
}
