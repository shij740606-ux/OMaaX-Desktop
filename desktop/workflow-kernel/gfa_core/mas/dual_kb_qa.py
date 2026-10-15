"""Client adapter for the gene-family-mas-v2 QA and graph services.

The bbav5 frontend expects a compact RAG-like payload
(`answer`, `sources`, `knowledge_graph`).  gene-family-mas-v2 exposes
separate QA and graph-QA routes, so this module centralizes the mapping.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

import requests


DUAL_KB_QA_BASE_URL = (
    os.environ.get("GENE_FAMILY_MAS_API")
    or os.environ.get("DUAL_KB_QA_BASE_URL")
    or "http://127.0.0.1:7902"
).rstrip("/")
DUAL_KB_QA_ROUTE = os.environ.get("DUAL_KB_QA_ROUTE", "kb-qa").strip().strip("/") or "kb-qa"
DUAL_KB_GRAPH_QA_ROUTE = os.environ.get("DUAL_KB_GRAPH_QA_ROUTE", "v15-graph-qa").strip().strip("/") or "v15-graph-qa"
DUAL_KB_QA_VARIANT = os.environ.get("DUAL_KB_QA_VARIANT", "full").strip() or "full"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def dual_kb_endpoint(path: str = "chat") -> str:
    return f"{DUAL_KB_QA_BASE_URL}/api/v1/{DUAL_KB_QA_ROUTE}/{path.strip('/')}"


def graph_qa_endpoint(path: str = "chat") -> str:
    return f"{DUAL_KB_QA_BASE_URL}/api/v1/{DUAL_KB_GRAPH_QA_ROUTE}/{path.strip('/')}"


def friendly_rag_progress_event(event: dict[str, Any]) -> dict[str, Any]:
    """Translate truthful QA milestones without inventing entities or counts."""
    stage = str(event.get("stage") or "")
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    message = ""
    if stage == "understanding_started":
        message = "正在理解你的问题并识别检索目标……"
    elif stage == "entities_identified":
        entities = [str(value).strip() for value in details.get("entities") or [] if str(value).strip()]
        message = f"我看到你关注的是：{'、'.join(entities)}。" if entities else "已识别检索目标，正在查找相关内容……"
    elif stage == "related_search_started":
        message = "正在知识库中查找相关内容……"
    elif stage == "concepts_found":
        concepts = [str(value).strip() for value in details.get("concepts") or [] if str(value).strip()]
        concept_count = int(details.get("concept_count") or 0)
        connection_count = int(details.get("connection_count") or 0)
        prefix = f"找到了相关概念：{'、'.join(concepts)}。" if concepts else "找到了相关概念。"
        if concept_count or connection_count:
            message = f"{prefix}已找到{concept_count}个相关概念和{connection_count}条知识连接，正在扩展相关证据……"
        else:
            message = f"{prefix}正在扩展相关证据……"
    elif stage == "subquestion_retrieved":
        candidate_count = int(details.get("candidate_count") or 0)
        message = (
            f"已检索到{candidate_count}条候选内容，Agent正在检查其与问题的相关性……"
            if candidate_count
            else "已检索到候选内容，Agent正在检查其与问题的相关性……"
        )
    elif stage == "agent_review_started":
        message = "Agent正在检查检索结果与问题是否匹配……"
    elif stage == "subquestion_retry_started":
        message = "部分内容证据不足，正在补充查找相关资料……"
    elif stage in {"evidence_accepted", "evidence_review_completed"}:
        accepted_count = int(details.get("accepted_count") or 0)
        publication_count = int(details.get("publication_count") or 0)
        if accepted_count or publication_count:
            message = f"Agent已确认{accepted_count}条相关证据，其中包括{publication_count}篇相关文献，正在组织回答……"
        else:
            message = "Agent已确认相关证据，正在组织回答……"
    elif stage == "answer_generation_started":
        message = "正在根据已确认的证据组织回答……"
    elif stage == "completed":
        message = "回答完成。"
    enriched = dict(event)
    enriched["message"] = message
    return enriched


def _sse_frame(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def dual_kb_info(timeout: float = 5.0) -> dict[str, Any]:
    response = requests.get(dual_kb_endpoint("info"), timeout=timeout)
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else response.text
        raise RuntimeError(f"Dual-KB QA info failed: HTTP {response.status_code}: {detail}")
    return payload if isinstance(payload, dict) else {}


def _normalize_source(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"content": str(item)}
    content = (
        item.get("content")
        or item.get("evidence")
        or " -- ".join(str(item.get(key, "")) for key in ("source", "relation", "target") if item.get(key))
    )
    normalized = dict(item)
    normalized.setdefault("content", content)
    normalized.setdefault("doc_id", item.get("doc_id") or item.get("paper_id") or item.get("pubmed_id") or item.get("id", ""))
    normalized.setdefault("score", item.get("score") or item.get("confidence") or 1.0)
    return normalized


def _graph_edges_to_knowledge_graph(edges: Any) -> dict[str, Any] | None:
    if not isinstance(edges, list) or not edges:
        return None
    nodes: dict[str, dict[str, Any]] = {}
    kg_edges: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not source or not target or source == target:
            continue
        nodes.setdefault(source, {"id": source, "type": edge.get("source_type") or "unknown"})
        nodes.setdefault(target, {"id": target, "type": edge.get("target_type") or "unknown"})
        kg_edges.append({
            "source": source,
            "target": target,
            "relation": edge.get("relation") or edge.get("label") or "",
        })
    if not nodes:
        return None
    return {"nodes": list(nodes.values()), "edges": kg_edges}


def _native_chains_to_knowledge_graph(chains: Any) -> dict[str, Any] | None:
    """Convert QA V1.1.73 native path chains to the frontend graph payload."""
    if isinstance(chains, dict):
        chains = chains.get("chains") or []
    if not isinstance(chains, list) or not chains:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or "")
        for hop_index, hop in enumerate(chain.get("hops") or [], 1):
            if not isinstance(hop, dict):
                continue
            source = str(hop.get("source") or "").strip()
            target = str(hop.get("target") or "").strip()
            relation = str(hop.get("relation") or "").strip()
            if not source or not target or source == target:
                continue
            edge_key = (source, relation, target)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            nodes.setdefault(source, {
                "id": source,
                "label": source,
                "type": hop.get("source_type") or "unknown",
            })
            nodes.setdefault(target, {
                "id": target,
                "label": target,
                "type": hop.get("target_type") or "unknown",
            })
            edges.append({
                "source": source,
                "target": target,
                "relation": relation,
                "label": relation,
                "chain_id": chain_id,
                "hop": hop_index,
                "paper_id": hop.get("paper_id") or "",
                "pmid": hop.get("pmid") or "",
                "pmcid": hop.get("pmcid") or "",
                "doi": hop.get("doi") or "",
            })
    if not nodes:
        return None
    return {"nodes": list(nodes.values()), "edges": edges}


def _query_graph_qa(question: str, *, top_k: int, use_llm: bool, timeout: float) -> dict[str, Any]:
    payload = {
        "question": question,
        "top_k": max(1, min(int(top_k or 10), 50)),
        "use_llm": bool(use_llm),
    }
    response = requests.post(graph_qa_endpoint("chat"), json=payload, timeout=timeout)
    data = response.json() if response.content else {}
    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else response.text
        raise RuntimeError(f"Graph QA chat failed: HTTP {response.status_code}: {detail}")
    return data if isinstance(data, dict) else {}


def _build_dual_kb_payload(
    question: str,
    *,
    top_k: int,
    variant: str | None,
    use_llm: bool | None,
    graph_hops: int,
    graph_limit_nodes: int,
    graph_limit_edges: int,
) -> tuple[str, dict[str, Any]]:
    route = DUAL_KB_QA_ROUTE.strip("/")
    resolved_use_llm = _env_bool("DUAL_KB_QA_USE_LLM", True) if use_llm is None else bool(use_llm)
    if route == "qa-v1-1-73":
        payload: dict[str, Any] = {
            "question": question,
            "debug": False,
            "use_llm": resolved_use_llm,
            "chain_top_k": 3,
            "chain_hops": max(1, min(int(graph_hops or 2), 2)),
            "chain_max_edges": 12,
            "chain_max_nodes": 10,
            "native_chain_enabled": True,
            "native_chain_max_hops": 3,
            "native_chain_top_k": 5,
        }
    else:
        payload = {
            "question": question,
            "top_k": max(1, min(int(top_k or 10), 40)),
            "use_llm": resolved_use_llm,
        }
    if os.environ.get("DUAL_KB_QA_USE_PROVISIONAL_DRAFT") is not None:
        payload["use_provisional_draft"] = _env_bool(
            "DUAL_KB_QA_USE_PROVISIONAL_DRAFT",
            False,
        )
    if route not in {"kb-qa", "v15-graph-qa", "v15-agentic-qa", "qa-v1-1-73"}:
        payload.update({
            "variant": variant or DUAL_KB_QA_VARIANT,
            "graph_hops": max(1, min(int(graph_hops or 2), 2)),
            "graph_limit_nodes": max(1, min(int(graph_limit_nodes or 60), 120)),
            "graph_limit_edges": max(1, min(int(graph_limit_edges or 100), 180)),
        })
    return route, payload


def _normalize_dual_kb_response(
    data: dict[str, Any],
    *,
    route: str,
    graph_data: dict[str, Any] | None = None,
    request_path: str = "chat",
) -> dict[str, Any]:
    graph_data = graph_data or {}
    answer = data.get("answer") or data.get("response") or data.get("message") or ""
    raw_sources = data.get("sources") or data.get("citations") or data.get("graph_edges") or []
    sources = [_normalize_source(item) for item in raw_sources]
    raw_used_sources = data.get("used_sources") or data.get("references") or raw_sources
    used_sources = [_normalize_source(item) for item in raw_used_sources]
    graph_edges = data.get("graph_edges") or graph_data.get("graph_edges") or []
    native_graph_chains = data.get("native_graph_chains") or []
    graph_knowledge_chains = data.get("graph_knowledge_chains") or {}
    knowledge_graph = (
        data.get("knowledge_graph")
        or _graph_edges_to_knowledge_graph(graph_edges)
        or _native_chains_to_knowledge_graph(native_graph_chains or graph_knowledge_chains)
    )
    normalized = {
        "success": bool(data.get("success", True)),
        "answer": answer,
        "response": answer,
        "sources": sources,
        "references": used_sources,
        "knowledge_graph": knowledge_graph,
        "kg_entities": data.get("kg_entities") or [],
        "mode": data.get("mode") or f"{route}_chat",
        "source_count": data.get("source_count", len(sources)),
        "used_sources": used_sources,
        "retrieval_plan": data.get("retrieval_plan") or data.get("agentic_plan") or {},
        "retrieval_notes": data.get("retrieval_notes") or data.get("notes") or [],
        "query_id": data.get("query_id") or "",
        "version": data.get("version") or "",
        "graph_knowledge_chains": graph_knowledge_chains,
        "native_graph_chains": native_graph_chains,
        "endpoint": dual_kb_endpoint(request_path),
        "graph_endpoint": graph_qa_endpoint("chat"),
    }
    if graph_edges:
        normalized["graph_edges"] = graph_edges
    if graph_data.get("error"):
        normalized.setdefault("retrieval_notes", []).append(f"Graph QA unavailable: {graph_data['error']}")
    if data.get("timing") is not None:
        normalized["timing"] = data.get("timing")
    if data.get("timings") is not None:
        normalized["timings"] = data.get("timings")
    if data.get("citation_map") is not None:
        normalized["citation_map"] = data.get("citation_map")
    return normalized


def query_dual_kb_qa(
    question: str,
    *,
    top_k: int = 10,
    variant: str | None = None,
    use_llm: bool | None = None,
    graph_hops: int = 2,
    graph_limit_nodes: int = 60,
    graph_limit_edges: int = 100,
    timeout: float = 240.0,
) -> dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        return {
            "success": True,
            "answer": "请输入要查询的问题。",
            "sources": [],
            "mode": "dual_kb_qa_empty_query",
        }

    route, payload = _build_dual_kb_payload(
        question,
        top_k=top_k,
        variant=variant,
        use_llm=use_llm,
        graph_hops=graph_hops,
        graph_limit_nodes=graph_limit_nodes,
        graph_limit_edges=graph_limit_edges,
    )
    effective_use_llm = bool(payload.get("use_llm"))
    request_path = "chat" if effective_use_llm else "search"

    request_timeout = (
        max(timeout, float(os.environ.get("QA_V1_1_73_TIMEOUT", "300")))
        if route == "qa-v1-1-73"
        else timeout
    )
    response = requests.post(
        dual_kb_endpoint(request_path),
        json=payload,
        timeout=request_timeout,
    )
    data = response.json() if response.content else {}
    initial_detail = data.get("detail") if isinstance(data, dict) else None
    if (
        effective_use_llm
        and response.status_code >= 400
        and isinstance(initial_detail, dict)
        and initial_detail.get("error_code") == "invalid_llm_citation"
    ):
        response = requests.post(
            dual_kb_endpoint(request_path),
            json=payload,
            timeout=request_timeout,
        )
        data = response.json() if response.content else {}
    if response.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else response.text
        raise RuntimeError(f"Dual-KB QA chat failed: HTTP {response.status_code}: {detail}")
    if not isinstance(data, dict):
        raise RuntimeError("Dual-KB QA chat returned a non-JSON-object payload")

    graph_data = {}
    if (
        effective_use_llm
        and not data.get("knowledge_graph")
        and route not in {DUAL_KB_GRAPH_QA_ROUTE, "qa-v1-1-73"}
    ):
        try:
            graph_data = _query_graph_qa(
                question,
                top_k=top_k,
                use_llm=False,
                timeout=min(timeout, 60.0),
            )
        except Exception as exc:
            graph_data = {"error": str(exc)}

    return _normalize_dual_kb_response(
        data,
        route=route,
        graph_data=graph_data,
        request_path=request_path,
    )


def stream_dual_kb_qa(
    question: str,
    *,
    top_k: int = 10,
    variant: str | None = None,
    use_llm: bool | None = None,
    graph_hops: int = 2,
    graph_limit_nodes: int = 60,
    graph_limit_edges: int = 100,
    timeout: float = 300.0,
) -> Iterator[str]:
    """Proxy truthful QA milestones and normalize the final V1.1.73 payload."""
    question = str(question or "").strip()
    if not question:
        yield _sse_frame("complete", {
            "success": True,
            "answer": "请输入要查询的问题。",
            "response": "请输入要查询的问题。",
            "sources": [],
            "references": [],
            "used_sources": [],
            "mode": "dual_kb_qa_empty_query",
        })
        return

    route, payload = _build_dual_kb_payload(
        question,
        top_k=top_k,
        variant=variant,
        use_llm=use_llm,
        graph_hops=graph_hops,
        graph_limit_nodes=graph_limit_nodes,
        graph_limit_edges=graph_limit_edges,
    )
    request_timeout = (
        max(timeout, float(os.environ.get("QA_V1_1_73_TIMEOUT", "300")))
        if route == "qa-v1-1-73"
        else timeout
    )
    try:
        response = requests.post(
            dual_kb_endpoint("chat/stream"),
            json=payload,
            stream=True,
            timeout=(10.0, request_timeout),
        )
    except requests.Timeout as exc:
        yield _sse_frame("error", {
            "success": False,
            "status": 504,
            "error_code": "request_timeout",
            "message": str(exc) or "QA stream request timed out",
        })
        return
    except requests.ConnectionError as exc:
        yield _sse_frame("error", {
            "success": False,
            "status": 503,
            "error_code": "service_unavailable",
            "message": str(exc) or "QA stream service unavailable",
        })
        return

    try:
        if response.status_code >= 400:
            try:
                body = response.json() if response.content else {}
            except Exception:
                body = {}
            detail = body.get("detail") if isinstance(body, dict) else {}
            detail = detail if isinstance(detail, dict) else {}
            yield _sse_frame("error", {
                "success": False,
                "status": response.status_code,
                "error_code": detail.get("error_code") or (
                    "stream_unavailable" if response.status_code in {404, 405} else "service_unavailable"
                ),
                "message": detail.get("message") or response.text or "QA stream request failed",
            })
            return

        event_name = "message"
        data_lines: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
            if line.startswith(":"):
                yield ": keepalive\n\n"
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
                continue
            if line != "":
                continue
            if not data_lines:
                event_name = "message"
                continue
            try:
                data = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                data = {
                    "success": False,
                    "status": 502,
                    "message": "Invalid upstream stream event",
                    "error_code": "request_failed",
                }
                event_name = "error"
            if event_name == "progress" and isinstance(data, dict):
                data = friendly_rag_progress_event(data)
            elif event_name == "complete" and isinstance(data, dict):
                data = _normalize_dual_kb_response(data, route=route)
            yield _sse_frame(event_name, data if isinstance(data, dict) else {"data": data})
            event_name = "message"
            data_lines = []
    except requests.Timeout as exc:
        yield _sse_frame("error", {
            "success": False,
            "status": 504,
            "error_code": "request_timeout",
            "message": str(exc) or "QA stream request timed out",
        })
    except requests.ConnectionError as exc:
        yield _sse_frame("error", {
            "success": False,
            "status": 503,
            "error_code": "service_unavailable",
            "message": str(exc) or "QA stream service unavailable",
        })
    finally:
        response.close()
