#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MOE 成語典（進階版 webMd=2）— 精準抽取 & 完整組版 v6
修正：
- 修掉使用 .format 導致的 '0,1' 例外
- 例句在主流程用成語 title 收斂：移除成語左右空白
- safe_slug 的多底線收斂 regex 修正
- 其餘同 v5：逐筆寫檔、JSON/TXT/JSONL、進度列、連續 not found 停止
"""

import re
import os
import json
import time
import argparse
from typing import List, Optional, Dict, Tuple
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://dict.idioms.moe.edu.tw"
VIEW = "/idiomView.jsp"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36 moe-idiom-adv-scraper/6.0"),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
    "Cache-Control": "no-cache",
}

BPMF = "\u3105-\u3129\u31A0-\u31BA\u02D9\u02CA\u02C7\u02CB"  # 注音＋擴充＋聲調
CJK  = "\u4E00-\u9FFF\u3400-\u4DBF"                           # CJK

# ---------- utils ----------
def add_params(url: str, **params) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    for k, v in params.items():
        q[k] = [str(v)]
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))

def get_soup(url: str, sess: Optional[requests.Session] = None) -> BeautifulSoup:
    s = sess or requests.Session()
    r = s.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def norm_space(s: str) -> str:
    if not s: return s
    s = re.sub(r"[ \t\u00A0\u200B]+", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    return s.strip()

def rm_blanklines(s: str) -> str:
    if not s: return s
    return re.sub(r"\n{2,}", "\n", s.strip())

def strip_newlines(s: str) -> str:
    return s.replace("\n", "") if s else s

def condense_zhuyin(s: str) -> str:
    if not s: return s
    return re.sub(rf"(?<=[{BPMF}])\s+(?=[{BPMF}])", "", s)

def strip_bpmf_adjacent_newlines(s: str) -> str:
    if not s: return s
    s = re.sub(rf"([{BPMF}])\s*\n+\s*", r"\1", s)
    s = re.sub(rf"\s*\n+\s*([{BPMF}])", r"\1", s)
    return s

def replace_arrow_notes(text: str) -> str:
    if not text: return text
    return re.sub(r"\n\s*(\d+)\s*[>〉]", r"[\1]", text)

def newline_to_cjk_comma(text: str) -> str:
    if not text: return text
    text = re.sub(rf"(?<=[{CJK}\]])\s*\n+\s*(?=[{CJK}“「『（(])", "，", text)
    text = text.replace("\n", "")
    text = re.sub(r"，{2,}", "，", text)
    return text

def fix_inline_markers_and_quotes(text: str) -> str:
    if not text: return text
    text = re.sub(r"\n\s*([＃△])\s*", r"\1", text)
    text = re.sub(r"([＃△])\s*\n\s*([「『（(《])", r"\1\2", text)
    text = re.sub(r"([「『（(《])\s*\n\s*", r"\1", text)
    text = re.sub(r"\s*\n\s*([」』）)》])", r"\1", text)
    return text

def only_article(soup: BeautifulSoup) -> Tag:
    art = soup.select_one("article#idiomPage") or soup.select_one("#mainContent") or soup
    for sel in ["script", "style", ".panel", ".panel2", ".banner2", "#goTop", "footer nav", "header nav"]:
        for t in art.select(sel): t.decompose()
    return art

def idiom_table(art: Tag) -> Optional[Tag]:
    return art.select_one("#idiomTab")

def td_by_th(table: Tag, th_regex: str) -> Optional[Tag]:
    if table is None: return None
    for tr in table.select("tr"):
        th = tr.find("th"); td = tr.find("td")
        if th and td and re.search(th_regex, th.get_text(" ", strip=True)):
            return td
    return None

def text_in_td(table: Tag, th_regex: str) -> str:
    td = td_by_th(table, th_regex)
    return norm_space(td.get_text("\n", strip=True)) if td else ""

# ---------- sections ----------
def parse_usage_td(td: Tag) -> Dict[str, object]:
    """
    只負責收集內容與基礎清理；真正「例句中成語左右空白移除」在主流程做（需要 title）。
    """
    if td is None:
        return {"usage_meaning": "", "usage_category": "", "usage_examples": []}
    meaning, category = "", ""
    examples: List[str] = []

    for h4 in td.find_all(["h4", "strong"]):
        title = h4.get_text(" ", strip=True)
        content_chunks: List[str] = []
        node = h4.next_sibling
        while node:
            if isinstance(node, Tag) and node.name in ("h4", "strong"):
                break
            if isinstance(node, Tag):
                if node.name == "ol":
                    for li in node.select("li"):
                        examples.append(strip_newlines(norm_space(li.get_text(" ", strip=True))))
                else:
                    content_chunks.append(norm_space(node.get_text(" ", strip=True)))
            node = node.next_sibling
        txt = norm_space("\n".join([c for c in content_chunks if c]))
        if "語義說明" in title:
            meaning = txt
        elif "使用類別" in title:
            category = txt
        elif "例句" in title:
            if txt:
                for line in re.split(r"\n+", txt):
                    line = strip_newlines(line)
                    if line:
                        examples.append(line)

    # 先把換行拿掉，內部空白收斂
    examples = [norm_space(strip_newlines(x)) for x in examples]
    return {"usage_meaning": meaning, "usage_category": category, "usage_examples": examples}

def parse_synonyms_antonyms_td(td: Tag) -> Dict[str, object]:
    """解析辨識欄位中的近義成語、反義成語和比較內容"""
    if td is None: 
        return {
            "synonyms": [], "synonym_links": [],
            "antonyms": [], "antonym_links": [], 
            "comparison": ""
        }
    
    synonyms, synonym_links = [], []
    antonyms, antonym_links = [], []
    comparison_parts = []
    
    # 解析近義成語
    for h in td.find_all(["h4", "strong"]):
        if "近義成語" in h.get_text(" ", strip=True):
            node = h.next_sibling
            while node:
                if isinstance(node, Tag) and node.name in ("h4", "strong"): 
                    break
                if isinstance(node, Tag):
                    for a in node.select("a[href]"):
                        txt = strip_newlines(norm_space(a.get_text(" ", strip=True)))
                        href = a.get("href", "")
                        if txt: synonyms.append(txt)
                        if href and href.startswith("/idiomView.jsp"):
                            synonym_links.append(BASE + href)
                    raw = norm_space(node.get_text(" ", strip=True))
                    for term in re.split(r"[、,，]\s*", raw):
                        term = strip_newlines(term)
                        if term and term not in synonyms and "近義成語" not in term and len(term) <= 8:
                            synonyms.append(term)
                node = node.next_sibling
    
    # 解析反義成語
    for h in td.find_all(["h4", "strong"]):
        if "反義成語" in h.get_text(" ", strip=True):
            node = h.next_sibling
            while node:
                if isinstance(node, Tag) and node.name in ("h4", "strong"): 
                    break
                if isinstance(node, Tag) and node.name != "p":  # 跳過包含比較內容的p標籤
                    for a in node.select("a[href]"):
                        txt = strip_newlines(norm_space(a.get_text(" ", strip=True)))
                        href = a.get("href", "")
                        if txt: antonyms.append(txt)
                        if href and href.startswith("/idiomView.jsp"):
                            antonym_links.append(BASE + href)
                    # 只處理純文字，不含連結的內容
                    raw = norm_space(node.get_text(" ", strip=True))
                    # 過濾掉包含比較說明或例句內容的部分
                    if not any(x in raw for x in ["及", "都有", "側重於", "例句", "我們已下", "希望大家", "∼"]):
                        for term in re.split(r"[、,，]\s*", raw):
                            term = strip_newlines(term)
                            if term and term not in antonyms and "反義成語" not in term and len(term) <= 8:
                                antonyms.append(term)
                node = node.next_sibling
    
    # 解析比較內容（如同異比較、例句比較表格等）
    for div in td.select("div.lab"):
        comparison_parts.append(strip_newlines(norm_space(div.get_text(" ", strip=True))))
    
    for table in td.select("table.compTab"):
        table_text = strip_newlines(norm_space(table.get_text(" ", strip=True)))
        if table_text:
            comparison_parts.append(table_text)
    
    # 去重
    out_syn, seen = [], set()
    for x in synonyms:
        if x and x not in seen: seen.add(x); out_syn.append(x)
    out_syn_links, seen2 = [], set()
    for u in synonym_links:
        if u and u not in seen2: seen2.add(u); out_syn_links.append(u)
        
    out_ant, seen3 = [], set()
    for x in antonyms:
        if x and x not in seen3: seen3.add(x); out_ant.append(x)
    out_ant_links, seen4 = [], set()
    for u in antonym_links:
        if u and u not in seen4: seen4.add(u); out_ant_links.append(u)
    
    comparison = "；".join(comparison_parts) if comparison_parts else ""
    
    return {
        "synonyms": out_syn, "synonym_links": out_syn_links,
        "antonyms": out_ant, "antonym_links": out_ant_links,
        "comparison": comparison
    }

def parse_citations_td(td: Tag) -> str:
    if td is None: return ""
    items = [norm_space(li.get_text(" ", strip=True)) for li in td.select("ol > li, ul > li")]
    return "\n".join(items) if items else norm_space(td.get_text("\n", strip=True))

# ---------- notes / source / references ----------
def split_source_title_body(source_all: str) -> Tuple[str, str]:
    if not source_all: return "", ""
    lines = [ln for ln in source_all.splitlines() if ln.strip()]
    if not lines: return "", ""
    title = lines[0]
    body  = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return title, body

def format_source_body(body: str) -> str:
    body = replace_arrow_notes(body)
    body = condense_zhuyin(body)
    body = strip_bpmf_adjacent_newlines(body)
    body = newline_to_cjk_comma(body)
    return body

def enumerate_source_notes(notes_raw: str) -> str:
    if not notes_raw: return ""
    txt = condense_zhuyin(notes_raw)
    txt = strip_bpmf_adjacent_newlines(txt)
    txt = rm_blanklines(txt)
    items = []
    pattern = re.compile(rf"(?m)^[ \t]*([{CJK}]+：.*?)(?=^[ \t]*[{CJK}]+：|\Z)", re.S)
    for i, m in enumerate(pattern.finditer(txt), 1):
        item = norm_space(m.group(1)).replace("\n", "")
        if not re.search(r"[。！？」』」)]$", item): item += "。"
        items.append(f"[{i}]{item}")
    return "".join(items)

CN_NUM = {1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九",10:"十"}

def cn_index(n: int) -> str:
    if n <= 10: return CN_NUM[n]
    if n < 20:  return "十" + CN_NUM[n-10]
    a, b = divmod(n, 10)
    return CN_NUM[a] + "十" + (CN_NUM[b] if b else "")

def format_references_for_fulltext(ref_raw: str) -> str:
    """簡化的參考詞語格式化，保留基本內容結構"""
    if not ref_raw: return ""
    # 基本清理
    s = condense_zhuyin(ref_raw)
    s = strip_bpmf_adjacent_newlines(s)
    s = rm_blanklines(s)
    
    # 簡單的行處理，保持可讀性
    lines = [line.strip() for line in s.splitlines() if line.strip()]
    if not lines: return ""
    
    # 將相關行組合成段落
    result_lines = []
    current_item = ""
    
    for line in lines:
        # 如果是成語名稱（4字以內的中文）
        if len(line) <= 6 and re.match(rf"^[{CJK}]+$", line):
            if current_item:
                result_lines.append(current_item.strip())
            current_item = line
        else:
            if current_item:
                current_item += line
            else:
                result_lines.append(line)
    
    if current_item:
        result_lines.append(current_item.strip())
    
    return "；".join(result_lines) if result_lines else ""

# ---------- main parse ----------
class NotFound(Exception): pass

def parse_idiom(id_value: str, sess: Optional[requests.Session] = None) -> Dict:
    url = add_params(f"{BASE}{VIEW}?ID={id_value}", webMd=2, la=0)
    soup = get_soup(url, sess)
    art  = only_article(soup)
    table = idiom_table(art)
    if not table:
        raise NotFound(f"ID={id_value} not found or not advanced page.")

    title    = text_in_td(table, r"^(成|詞)\s*語$")
    bopomofo = text_in_td(table, r"^注\s*音$")
    pinyin   = text_in_td(table, r"^漢語拼音$")

    bopomofo = strip_newlines(condense_zhuyin(bopomofo))

    definition = text_in_td(table, r"^釋\s*義$")
    definition = replace_arrow_notes(definition)
    definition = condense_zhuyin(definition)
    definition = strip_bpmf_adjacent_newlines(definition)
    definition = fix_inline_markers_and_quotes(definition)

    td_source = td_by_th(table, r"^典\s*源$")
    source_all = norm_space(td_source.get_text("\n", strip=True)) if td_source else ""
    parts = re.split(r"\n?\s*〔?注解〕?\s*\n?", source_all, maxsplit=1)
    source_text = parts[0] if parts else ""
    source_notes_raw = parts[1] if len(parts) > 1 else ""
    source_title, source_body_raw = split_source_title_body(source_text)
    source_body  = format_source_body(source_body_raw)
    source_notes = enumerate_source_notes(source_notes_raw)

    story = text_in_td(table, r"^典故說明$")
    story = condense_zhuyin(story)
    story = strip_bpmf_adjacent_newlines(story)
    story = fix_inline_markers_and_quotes(story)

    citations = parse_citations_td(td_by_th(table, r"^書\s*證$"))
    citations = condense_zhuyin(citations)
    citations = strip_bpmf_adjacent_newlines(citations)

    usage     = parse_usage_td(td_by_th(table, r"^用法說明$"))
    usage_meaning  = usage["usage_meaning"]
    usage_category = usage["usage_category"]
    usage_examples = usage["usage_examples"]

    # 在此精準移除「例句」中成語左右空白（用 title 收斂）
    if title:
        pat = re.compile(rf"\s*{re.escape(title)}\s*")
        usage_examples = [pat.sub(title, ex) for ex in usage_examples]

    syn_ant   = parse_synonyms_antonyms_td(td_by_th(table, r"^辨\s*識$"))
    synonyms       = [strip_newlines(x) for x in syn_ant["synonyms"]]
    synonym_links  = syn_ant["synonym_links"]
    antonyms       = [strip_newlines(x) for x in syn_ant["antonyms"]]
    antonym_links  = syn_ant["antonym_links"]
    comparison     = syn_ant["comparison"]

    references_raw = text_in_td(table, r"參考詞語")
    references_fmt = format_references_for_fulltext(references_raw)

    # fulltext
    lines = []
    lines.append(f"成語：{title}")
    lines.append(f"注音：{bopomofo}")
    lines.append(f"漢語拼音：{pinyin}")
    lines.append(f"釋義：{definition}")
    if source_title: 
        if source_body:
            if source_title == '＃':
                lines.append(f"典源：" + source_body)
            else:
                lines.append(f"典源：{source_title}" + source_body)
    if source_notes: lines.append(f"注解：{source_notes}")
    if story:        lines.append(f"典故說明：{story}")
    if citations:    lines.append(f"書證：{citations}")
    if usage_examples:
        lines.append("例句：" + "；".join(usage_examples))
    if synonyms:
        lines.append("近義成語：" + "、".join(synonyms))
    if antonyms:
        lines.append("反義成語：" + "、".join(antonyms))
    if comparison:
        lines.append("比較說明：" + comparison)
    if references_fmt:
        lines.append("參考詞語：" + references_fmt)
    fulltext = rm_blanklines("\n".join(lines))

    # 數據有效性檢查
    if not title or not title.strip():
        raise NotFound(f"ID={id_value} title is empty or invalid")
    
    # 檢查是否為無意義的標點符號或過短內容
    title_clean = re.sub(r'[^\w\u4e00-\u9fff]', '', title.strip())
    if len(title_clean) < 2:
        raise NotFound(f"ID={id_value} title too short or meaningless: {title}")
    
    # 檢查是否缺少基本內容（注音和釋義至少要有一個有內容）
    if not bopomofo.strip() and not definition.strip():
        raise NotFound(f"ID={id_value} missing basic content (both bopomofo and definition are empty)")

    return {
        "id": str(id_value),
        "url": url,
        "title": title,
        "bopomofo": bopomofo,
        "pinyin": pinyin,
        "definition": definition,
        "source_title": source_title,
        "source": source_body,
        "source_notes": source_notes,
        "story": story,
        "citations": citations,
        "usage_meaning": usage_meaning,
        "usage_category": usage_category,
        "usage_examples": usage_examples,
        "synonyms": synonyms,
        "synonym_links": synonym_links,
        "antonyms": antonyms,
        "antonym_links": antonym_links,
        "comparison": comparison,
        "references": references_raw,
        "fulltext": fulltext,
    }

# ---------- I/O & loop ----------
def safe_slug(s: str) -> str:
    if not s: return "NA"
    s = s.strip()
    s = re.sub(rf"[^\w\-{CJK}]+", "_", s)
    s = re.sub(r"_{2,}", "_", s)   # 修正：用量詞而非 "{{2,}}"
    s = s.strip("_")
    return s or "NA"

def write_item(out_dir: str, data: Dict):
    title_slug = safe_slug(data.get("title", ""))
    base = f"{data['id']}_{title_slug}"
    json_dir = os.path.join(out_dir, "json")
    txt_dir  = os.path.join(out_dir, "txt")
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(txt_dir,  exist_ok=True)
    js_path = os.path.join(json_dir, base + ".json")
    tx_path = os.path.join(txt_dir,  base + ".txt")
    jl_path = os.path.join(out_dir,  "moe_idioms.jsonl")

    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(tx_path, "w", encoding="utf-8") as f:
        f.write(data["fulltext"] + "\n")
    with open(jl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
    return js_path, tx_path, jl_path

class NotFound(Exception): pass

def main():
    ap = argparse.ArgumentParser(description="Scrape MOE Idiom Advanced (webMd=2) → JSON/TXT per idiom + JSONL (v6)")
    ap.add_argument("--start-id", type=int, required=True, help="起始 ID（可為負數，例如 -1）")
    ap.add_argument("--step", type=int, required=True, help="步進（負值往負向，正值往正向）")
    ap.add_argument("--out-dir", default="out_moe_v6", help="輸出資料夾")
    ap.add_argument("--max-misses", type=int, default=20, help="連續不存在上限（達到即停止）")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    s = requests.Session()

    idx = args.start_id
    consecutive_miss = 0
    ok_cnt = 0
    miss_cnt = 0
    err_cnt = 0

    print("[開始] 進階版（webMd=2）逐 ID 擷取")
    while True:
        try:
            data = parse_idiom(str(idx), s)
            js, tx, jl = write_item(args.out_dir, data)
            ok_cnt += 1
            consecutive_miss = 0
            print(f"\r✔ 成功 {ok_cnt}　✗ 不存在 {miss_cnt}　⚠ 其他錯誤 {err_cnt}　| 目前 ID={idx}《{data.get('title','')}》", end="", flush=True)
        except NotFound:
            miss_cnt += 1
            consecutive_miss += 1
            print(f"\r… 略過（不存在或非進階頁） ID={idx}｜連續不存在={consecutive_miss}", end="", flush=True)
            if consecutive_miss >= args.max_misses:
                print()
                print(f"[END] 連續 {args.max_misses} 筆不存在，停止。最後 ID={idx}")
                break
        except Exception as e:
            err_cnt += 1
            consecutive_miss = 0
            print(f"\n  !! 解析失敗 ID={idx}：{e}")

        idx += args.step
        time.sleep(0.1)

    print(f"\n[完成] OK={ok_cnt}  NotFound={miss_cnt}  Error={err_cnt}")
    print(f"輸出資料夾：{os.path.abspath(args.out_dir)}")
    print("子資料夾：json/（每筆 JSON）、txt/（每筆 fulltext），以及 moe_idioms.jsonl（彙整）")

if __name__ == "__main__":
    main()
