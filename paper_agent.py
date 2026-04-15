#!/usr/bin/env python3
"""
논문 수집 및 요약 에이전트
arXiv에서 논문을 검색하고 Claude API를 사용하여 한국어로 요약합니다.

사용법:
    python paper_agent.py "검색어" -n 5
    python paper_agent.py "transformer attention mechanism" -n 10 -o ./results
"""

import os
import json
import argparse
from datetime import datetime
import anthropic
import arxiv


def search_papers(query: str, max_results: int = 10) -> list[dict]:
    """arXiv에서 논문을 검색하고 메타데이터를 반환합니다."""
    client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    papers = []
    for result in client.results(search):
        papers.append({
            "id": result.entry_id,
            "title": result.title.strip(),
            "authors": [author.name for author in result.authors],
            "abstract": result.summary.strip(),
            "pdf_url": result.pdf_url,
            "published": result.published.strftime("%Y-%m-%d"),
            "categories": result.categories,
        })

    return papers


def summarize_paper(paper: dict, client: anthropic.Anthropic) -> str:
    """Claude API를 사용하여 논문을 한국어로 요약합니다."""
    authors_str = ", ".join(paper["authors"][:5])
    if len(paper["authors"]) > 5:
        authors_str += f" 외 {len(paper['authors']) - 5}명"

    prompt = f"""다음 논문의 초록을 읽고 한국어로 구조화된 요약을 작성해주세요.

제목: {paper['title']}
저자: {authors_str}
발표일: {paper['published']}
카테고리: {', '.join(paper['categories'])}

초록:
{paper['abstract']}

아래 형식으로 요약해주세요:

**핵심 기여**
이 논문의 주요 기여점을 2-3문장으로 설명해주세요.

**방법론**
사용된 방법이나 기술적 접근법을 2-3문장으로 설명해주세요.

**주요 결과**
실험 결과나 주요 발견사항을 2-3문장으로 설명해주세요.

**연구 의의**
이 연구가 해당 분야에 미치는 영향과 활용 가능성을 1-2문장으로 설명해주세요."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def save_results(papers: list[dict], summaries: list[str], query: str, output_dir: str) -> tuple[str, str]:
    """결과를 JSON과 Markdown 파일로 저장합니다."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = query[:30].replace("/", "_").replace("\\", "_").replace(" ", "_")

    # JSON 저장
    json_data = {
        "query": query,
        "timestamp": timestamp,
        "total_papers": len(papers),
        "papers": [
            {**paper, "summary": summary}
            for paper, summary in zip(papers, summaries)
        ],
    }
    json_path = os.path.join(output_dir, f"{safe_query}_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    # Markdown 저장
    md_path = os.path.join(output_dir, f"{safe_query}_{timestamp}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 논문 수집 결과\n\n")
        f.write(f"| 항목 | 내용 |\n")
        f.write(f"|------|------|\n")
        f.write(f"| 검색어 | `{query}` |\n")
        f.write(f"| 수집일 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n")
        f.write(f"| 논문 수 | {len(papers)}편 |\n\n")
        f.write("---\n\n")

        for i, (paper, summary) in enumerate(zip(papers, summaries), 1):
            authors_display = ", ".join(paper["authors"][:3])
            if len(paper["authors"]) > 3:
                authors_display += f" 외 {len(paper['authors']) - 3}명"

            f.write(f"## {i}. {paper['title']}\n\n")
            f.write(f"- **저자**: {authors_display}\n")
            f.write(f"- **발표일**: {paper['published']}\n")
            f.write(f"- **카테고리**: {', '.join(paper['categories'])}\n")
            f.write(f"- **PDF**: [{paper['pdf_url']}]({paper['pdf_url']})\n\n")
            f.write(f"### 요약\n\n{summary}\n\n")
            f.write("---\n\n")

    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="arXiv 논문 수집 및 Claude AI 요약 에이전트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python paper_agent.py "large language model"
  python paper_agent.py "diffusion model image generation" -n 10
  python paper_agent.py "reinforcement learning robotics" -n 5 -o ./results
        """,
    )
    parser.add_argument("query", help="검색할 논문 주제 (영어 권장)")
    parser.add_argument(
        "-n", "--num-papers",
        type=int,
        default=5,
        metavar="N",
        help="수집할 논문 수 (기본값: 5, 최대 50)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="./output",
        metavar="DIR",
        help="결과 저장 디렉토리 (기본값: ./output)",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API 키 (미입력 시 ANTHROPIC_API_KEY 환경변수 사용)",
    )
    args = parser.parse_args()

    # API 키 확인
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[오류] Anthropic API 키가 필요합니다.")
        print("  방법 1: ANTHROPIC_API_KEY 환경변수 설정")
        print("  방법 2: --api-key 옵션으로 직접 전달")
        return 1

    num_papers = max(1, min(50, args.num_papers))

    print(f"검색어  : {args.query}")
    print(f"논문 수 : {num_papers}편")
    print(f"저장 위치: {os.path.abspath(args.output_dir)}")
    print()

    # 1단계: 논문 검색
    print("arXiv에서 논문을 검색 중...")
    papers = search_papers(args.query, num_papers)

    if not papers:
        print("검색 결과가 없습니다. 다른 검색어를 시도해보세요.")
        return 1

    print(f"{len(papers)}편의 논문을 찾았습니다.\n")

    # 2단계: Claude로 요약
    claude = anthropic.Anthropic(api_key=api_key)
    summaries = []

    for i, paper in enumerate(papers, 1):
        title_preview = paper["title"][:60] + ("..." if len(paper["title"]) > 60 else "")
        print(f"[{i}/{len(papers)}] 요약 중: {title_preview}")
        summary = summarize_paper(paper, claude)
        summaries.append(summary)

    # 3단계: 결과 저장
    print("\n결과를 저장 중...")
    json_path, md_path = save_results(papers, summaries, args.query, args.output_dir)

    print(f"\n완료! 저장된 파일:")
    print(f"  Markdown : {md_path}")
    print(f"  JSON     : {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
