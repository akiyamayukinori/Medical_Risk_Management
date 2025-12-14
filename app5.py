import streamlit as st
import json
import re
import os
import requests
from bs4 import BeautifulSoup
import pdfplumber
import io
import pandas as pd
from collections import defaultdict
from typing import List, Dict, Any
from datetime import datetime

# ==========================================
# 1. 設定・定数定義
# ==========================================
# ローカル実行時はファイルが作成されますが、Streamlit Cloudではセッションが切れると削除されます。
DATASET_PATH = "incident_dataset.json"
CHECKLISTS_PATH = "generated_checklists.json"

# ★★★★★ ここがスクレイピングのターゲットURLです ★★★★★
TARGET_URLS = [
    "https://www.med-safe.jp/report/index.html",  # 医療安全情報（主にPDFリンク集）
    "https://www.med-safe.jp/medical_safety/index.html",  # 医療事故情報収集等事業
]

# 修正1: 脳神経外科特有の処置と管理項目を追加
PROCEDURES = {
    "患者確認・指導": ["患者", "確認", "指導", "説明", "同意", "アレルギー"],
    "採血": ["採血", "血液", "静脈", "血管", "穿刺"],
    "輸血": ["輸血", "血液製剤", "血液型", "ポンピング"],
    "点滴・薬剤": ["点滴", "輸液", "IV", "薬剤投与", "シリンジポンプ", "輸液ポンプ", "抗凝固薬", "抗てんかん薬"],
    "手術": ["手術", "オペ", "術中", "麻酔", "執刀", "ガーゼカウント"],
    "内視鏡": ["内視鏡", "胃カメラ", "大腸", "スコープ", "CF", "GF"],
    "気管挿管": ["挿管", "気道", "換気", "チューブ", "抜管"],
    "中心静脈カテーテル": ["CVC", "中心静脈", "カテーテル", "CV", "ガイドワイヤー"],
    "ドレナージ管理": ["ドレナージ", "脳室", "腰椎", "シャント", "髄液"],
    "脳神経外科管理": ["意識レベル", "瞳孔", "麻痺", "頭蓋内圧", "クッシング"],
}

# チェックリスト項目抽出用キーワード
ACTION_KEYWORDS = [
    "確認", "照合", "二重", "固定", "緩める", "実施", "記録", "徹底", "維持", "変更",
    "抜針", "駆血帯", "止血", "部位", "選択", "アセスメント", "把握", "指示", "遵守",
    "識別", "注意", "カウント", "測定", "比較", "観察"
]

# ノイズ除去キーワード (変更なし)
NOISE_KEYWORDS = [
    "再発防止に努める", "ご理解いただければ幸い", "情報提供と位置づけ", "施行されている",
    "再発防止に向けて取り組んでいる姿を", "再発防止に資する", "情報提供と位置づけております",
    "ヒヤリ・ハット事例収集事業", "資料３", "全般コード化情報", "製造（輸入販売）業者名",
    "定点医療機関一覧", "平成", "月日現在", "定点医療機関とは", "事故の内容医療",
    "発生場面", "事例の概要", "全般コード化", "原因分析", "再発防止策", "実施した医療行為の目的",
    "検討結果", "病院名", "部門名", "職種", "性別", "年齢", "購入年月", "1517", "16",
    "発生要因", "対応と対策", "経過と結末", "背景要因", "別紙", "参照"
]

# 修正2: 脳神経外科病棟向けのチェックリスト項目を追加・拡充
STANDARD_CHECKLIST_ITEMS: Dict[str, List[str]] = {
    # 既存の項目 (例: 輸血) は維持
    "輸血": [
        "【準備】同意書の確認および患者への説明を行いましたか？",
        "【準備】交差適合試験の結果と血液製剤、指示書の内容（患者氏名、血液型、放射線照射有無）が一致しているか確認しましたか？",
        "【実施前】患者氏名、ID、血液型、製剤の有効期限、外観（凝集・変色・破損）を医師・看護師の2名で声出し確認しましたか？",
        "【実施中】投与開始直前および開始後5分、15分にバイタルサインを測定・観察しましたか？",
        "【実施後】副作用の有無を確認し、空バッグを所定の方法で保管・廃棄しましたか？"
    ],
    
    # 脳神経外科で特に重要な項目を個別に追加
    "患者確認・指導": [
        "【確認】患者の氏名とIDをリストバンドと照合し、本人に名乗ってもらい確認しましたか？",
        "【確認】アレルギー歴（特に造影剤アレルギー）を再確認し、記録しましたか？",
        "【指導】処置・検査前に、体動リスクを評価し、体動しないよう具体的かつ簡潔に説明しましたか？",
        "【説明】患者または家族に対し、これから行う処置や治療内容を説明し、同意を得ましたか？",
    ],

    "点滴・薬剤": [
        "【FIVE-RIGHTs】医師・薬剤師の指示書に基づき、正しい薬剤、量、時間、経路であることをダブルチェックしましたか？",
        "【抗凝固薬】手術や侵襲的処置の前に、休薬指示と最終投与時間を確認しましたか？",
        "【高浸透圧薬】Mannitolなどの高浸透圧薬に結晶化や沈殿物がないか確認し、投与速度は指示通りですか？",
        "【抗てんかん薬】処方開始・変更時に、適切な血中濃度採血オーダーがされているか確認しましたか？",
        "【持続点滴】ポンプ設定（薬剤名、単位、設定量）を2名のスタッフで声出し確認しましたか？",
        "【管理】麻薬・向精神薬は投与前後の残薬確認、記録、施錠保管を複数人で行いましたか？",
    ],
    
    "中心静脈カテーテル": [
        "【準備】エコーガイド下穿刺の準備（プローブカバー等）はできていますか？",
        "【実施中】ガイドワイヤー挿入時、抵抗がないことを確認しましたか？（無理な挿入は禁止）",
        "【実施中】動脈穿刺の除外（短軸・長軸像での確認、圧波形など）を行いましたか？",
        "【実施後】ガイドワイヤーが体内に残存していないことを本数確認しましたか？",
        "【実施後】カテーテル先端位置確認のためのX線撮影オーダーを行いましたか？",
        "【観察】刺入部の感染兆候（発赤・腫脹）の有無を毎日チェックしましたか？",
    ],
    
    "ドレナージ管理": [
        "【指示確認】ドレナージバッグの**高さ（cmH2O）**、クランプ・開放指示が明確ですか？",
        "【操作確認】体位変換や移送前後で、指示されたドレナージラインのクランプ操作を確実に実施しましたか？",
        "【排液観察】排液の**量（時間毎）**、色、混濁を記録し、急激な変化や異常な量はありませんか？",
        "【閉塞確認】ラインの屈曲、閉塞がないか確認しましたか？ ",
        "【刺入部】刺入部に感染兆候がないか確認し、無菌操作でドレッシング材を交換しましたか？",
    ],

    "脳神経外科管理": [
        "【意識レベル】JCSまたはGCSに基づき、正確かつ経時的に意識レベルを評価・記録しましたか？",
        "【瞳孔所見】瞳孔径と対光反射を左右で比較し、急激な**左右差の出現**や**散瞳**がないか確認しましたか？",
        "【麻痺評価】運動麻痺や感覚麻痺の有無、および昨日からの**進行・悪化**がないか詳細に評価しましたか？",
        "【バイタル】**クッシング現象**（徐脈、血圧上昇）などの頭蓋内圧亢進症状のサインがないか確認しましたか？",
        "【緊急体制】意識障害や呼吸状態の急変時、どの医師に**何分以内**に連絡するか確認されていますか？",
    ],

    # 既存の項目（採血、手術、気管挿管、内視鏡）は変更なしで維持
    "採血": [
        "【準備】検査指示書と採血管のラベル（氏名、ID、検査項目）を照合しましたか？",
        "【実施前】患者本人に氏名を名乗ってもらい、リストバンドと照合しましたか？",
        "【実施中】神経損傷予防のため、穿刺時の激痛やしびれの有無を患者に確認しましたか？",
        "【実施中】駆血帯は1分以内に解除しましたか？（特に抜針前の解除忘れに注意）",
        "【実施後】止血確認を行い、採血管の転倒混和を適切に行いましたか？"
    ],
    "手術": [
        "【Sign In】患者確認、手術部位、術式の確認、麻酔器・モニターのチェックは完了しましたか？",
        "【Time Out】執刀直前に全スタッフの手が止まり、患者名・術式・部位・予想される危険操作を全員で共有しましたか？",
        "【Time Out】予防的抗菌薬の投与は執刀60分以内に行われましたか？",
        "【Sign Out】ガーゼ・器械・縫合針のカウント数は一致しましたか？",
        "【Sign Out】摘出標本のラベル（患者名・検体名）は正しいですか？"
    ],
    "気管挿管": [
        "【準備】喉頭鏡のライト点灯、カフの破損がないか確認しましたか？",
        "【準備】困難気道が予想される場合、ビデオ喉頭鏡やブジーなどの代替器具を準備しましたか？",
        "【実施中】挿管後、聴診（5点聴診）およびカプノメータで二酸化炭素の波形を確認しましたか？",
        "【実施後】チューブの固定位置（歯列のcm）を記録し、確実に固定しましたか？",
        "【実施後】胸部X線でチューブ先端位置を確認しましたか？"
    ],
    "内視鏡": [
        "【準備】内視鏡洗浄消毒履歴を確認し、使用機器の動作確認を行いましたか？",
        "【実施前】抗血栓薬の休薬状況、アレルギー歴、既往歴を確認しましたか？",
        "【実施前】鎮静を行う場合、同意書の確認と蘇生用具（酸素、アンビュー等）の準備はできていますか？",
        "【実施中】患者のSpO2、呼吸状態、血圧のモニタリングを継続していますか？",
        "【実施後】覚醒状態を確認し、飲水・食事開始の指示を明確にしましたか？"
    ]
}


# ==========================================
# 2. ロジック関数群
# ==========================================

def load_data() -> List[Dict]:
    """インシデントデータセットを読み込む"""
    try:
        if os.path.exists(DATASET_PATH):
            with open(DATASET_PATH, "r", encoding="utf-8", errors='ignore') as f:
                return json.load(f)
        return []
    except Exception:
        if os.path.exists(DATASET_PATH):
            os.remove(DATASET_PATH)
        return []


def save_data(data: List[Dict]):
    """インシデントデータセットを保存する"""
    try:
        with open(DATASET_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@st.cache_data
def load_checklists() -> Dict[str, str]:
    """チェックリストデータを読み込む (キャッシュ対象)"""
    try:
        if not os.path.exists(CHECKLISTS_PATH):
            return {}
            
        with open(CHECKLISTS_PATH, "r", encoding="utf-8", errors='ignore') as f:
            return json.load(f)
    except Exception:
        return {}


def classify_procedure(text: str) -> str:
    """テキストから処置・手術の種類を分類する"""
    if not text:
        return "その他"
    for proc, words in PROCEDURES.items():
        if any(w in text for w in words):
            return proc
    return "その他"


def is_likely_garbled(text: str) -> bool:
    """テキストが文字化けしている可能性が高いか判定する。"""
    if not text or len(text) < 5:
        return True

    total_len = len(text)
    japanese_pattern = re.compile(r'[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u0020-\u007E\uff00-\uffef]')
    valid_chars_count = len(japanese_pattern.findall(text))
    valid_ratio = valid_chars_count / total_len

    if valid_ratio < 0.1:
        return True
    if re.search(r'https?://', text) or re.search(r'[a-zA-Z]{3,4}://', text):
        return True

    return False


def extract_action_items(prevention_text: str) -> List[str]:
    """具体的アクションに基づいてチェックリスト項目を抽出する"""
    actions = []
    sentences = re.split(r'[。\n]', prevention_text)

    for s in sentences:
        s = s.strip()
        if not s: continue
        if len(s) < 5 or len(s) > 100: continue
        if any(noise in s for noise in NOISE_KEYWORDS): continue

        if any(action in s for action in ACTION_KEYWORDS):
            cleaned_s = re.sub(r'[、。]$', '', s)
            cleaned_s = re.sub(r'^[-\d\.\s・]+', '', cleaned_s).strip()
            actions.append(cleaned_s)
    return actions


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出し、強力な文字化け除去を行う"""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if pdf.pages:
                page = pdf.pages[0]
                text = page.extract_text(errors='ignore') or ""

        text_bytes = text.encode('utf-8', errors='ignore')
        text = text_bytes.decode('utf-8', errors='ignore')

        text = text.replace('\ufffd', '')
        text = re.sub(r'[\x00-\x1F\x7F]', '', text)
        text = text.replace(u'\xa0', u' ').replace('　', ' ')

        text = re.sub(r'\s+', ' ', text).strip()

        for noise in NOISE_KEYWORDS:
            text = text.replace(noise, '')

        allowed_chars_regex = r'[^\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u3000-\u303F\u0020-\u007E\uff10-\uff19\n、。]'
        text = re.sub(allowed_chars_regex, '', text)

        return text
    except Exception:
        return ""


def parse_report_text(text: str, source_url: str) -> Dict[str, str]:
    """テキストから原因と対策を切り出す（簡易版）"""
    description = "抽出不可"
    cause = ""
    prevention = ""

    if "概要" in text:
        parts = text.split("概要")
        if len(parts) > 1: description = parts[1][:200]

    if "原因" in text:
        parts = text.split("原因")
        if len(parts) > 1: cause = parts[1][:200]

    if "対策" in text:
        parts = text.split("対策")
        if len(parts) > 1: prevention = parts[1][:300]
    elif "再発防止" in text:
        parts = text.split("再発防止")
        if len(parts) > 1: prevention = parts[1][:300]

    if len(description) < 10:
        description = text[:200]

    return {
        "source": source_url,
        "date": datetime.now().strftime("2025-12-01"),
        "department": "PDF解析",
        "incident_type": classify_procedure(description),
        "description": description.replace('\n', ' ').strip(),
        "cause": cause.replace('\n', ' ').strip(),
        "prevention": prevention.replace('\n', ' ').strip(),
        "impact": "不明"
    }


def scrape_pdf_links() -> List[str]:
    """ターゲットURLからPDFリンクを収集"""
    pdf_links = set()
    base_url = "https://www.med-safe.jp"
    for url in TARGET_URLS:
        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            for link in soup.find_all('a', href=True):
                href = link['href']
                if href.lower().endswith('.pdf'):
                    if href.startswith('/'):
                        abs_url = base_url + href
                    elif not href.startswith('http'):
                        abs_url = requests.compat.urljoin(url, href)
                    else:
                        abs_url = href
                    pdf_links.add(abs_url)
        except Exception:
            pass
    return list(pdf_links)


def scrape_and_update_dataset(limit_pdfs: int = 5) -> List[Dict]:
    """WebからPDFを取得してデータセットを更新"""
    pdf_urls = scrape_pdf_links()
    new_incidents: List[Dict] = []

    my_bar = st.progress(0)
    status_text = st.empty()

    target_pdfs = pdf_urls[:limit_pdfs]
    total = len(target_pdfs)

    for i, pdf_url in enumerate(target_pdfs):
        status_text.text(f"PDF解析中 ({i + 1}/{total}): {pdf_url}")
        my_bar.progress((i + 1) / total)
        try:
            pdf_response = requests.get(pdf_url, timeout=30)
            raw_text = extract_text_from_pdf(pdf_response.content)
            if len(raw_text) > 50:
                record = parse_report_text(raw_text, pdf_url)
                new_incidents.append(record)
        except Exception:
            pass

    status_text.empty()
    my_bar.empty()

    current_data = load_data()
    combined_data = current_data + new_incidents
    save_data(combined_data)
    return combined_data


def run_checklist_generation(incidents: List[Dict]):
    """インシデントデータと標準項目からチェックリストを生成"""
    causes = defaultdict(list)
    preventions_items = defaultdict(list)

    filtered_incidents = [item for item in incidents if not is_likely_garbled(item.get("description", ""))]

    for item in filtered_incidents:
        proc = classify_procedure(item.get("description", ""))
        cause = item.get("cause", "")
        prevention = item.get("prevention", "")
        if cause: causes[proc].append(cause.strip())
        if prevention:
            preventions_items[proc].extend(extract_action_items(prevention))

    checklists: Dict[str, str] = {}
    
    # PROCEDURESのキーを全て取得し、ソートしてループする
    all_procedures = sorted(list(STANDARD_CHECKLIST_ITEMS.keys()) + ["その他"])

    for proc in all_procedures:
        checklist: List[str] = []

        # 1. 標準チェック項目 (★必ず表示★)
        standard_items = STANDARD_CHECKLIST_ITEMS.get(proc, [])
        if standard_items:
            checklist.append(f"### 【標準安全手順（{proc}）】")
            # 確実な箇条書きのためのMarkdownリスト記号を追加
            for p in standard_items: checklist.append(f"- ✅ {p}")

        # 2. 事例からの追加項目
        unique_actions = sorted(list(set(preventions_items[proc])))
        filtered_actions = [a for a in unique_actions if a not in standard_items]
        if filtered_actions:
            if checklist: checklist.append("")
            checklist.append("### 【過去の事例に学ぶ追加チェック】")
            # 確実な箇条書きのためのMarkdownリスト記号を追加
            for p in filtered_actions: checklist.append(f"- □ {p}")

        # 3. 原因
        unique_causes = sorted(list(set(causes[proc])))
        if unique_causes:
            if checklist: checklist.append("")
            checklist.append("#### (参考) 過去の主な原因")
            # 確実な箇条書きのためのMarkdownリスト記号を追加
            for c in unique_causes: checklist.append(f"- {c}")

        if checklist:
            checklists[proc] = "\n".join(checklist)

    # st.cache_dataをクリアし、新しいチェックリストを保存
    st.cache_data.clear()  
    with open(CHECKLISTS_PATH, "w", encoding="utf-8") as f:
        json.dump(checklists, f, ensure_ascii=False, indent=2)


def reset_system(limit_pdfs: int):
    """システムをリセットし再構築する"""
    if os.path.exists(DATASET_PATH): os.remove(DATASET_PATH)
    if os.path.exists(CHECKLISTS_PATH): os.remove(CHECKLISTS_PATH)

    incidents = scrape_and_update_dataset(limit_pdfs)
    run_checklist_generation(incidents)
    return incidents


# ==========================================
# 3. UI (Streamlit Pages)
# ==========================================

# ★★★ page_viewer() 関数 (st.checkbox + st.session_stateによる状態保持) ★★★
def page_viewer():
    st.title("📋 医療安全チェックリスト")
    
    if not os.path.exists(CHECKLISTS_PATH):
        st.warning("⚠️ チェックリストファイルが生成されていません。データ管理・更新ページで生成してください。")
        checklists = {}
    else:
        checklists = load_checklists()

    procedures = sorted(list(STANDARD_CHECKLIST_ITEMS.keys()) + ["その他"])
    
    default_index = 0
    if "脳神経外科管理" in procedures:
        default_index = procedures.index("脳神経外科管理")
    elif "輸血" in procedures:
        default_index = procedures.index("輸血")

    selected_proc = st.selectbox("処置を選択してください", procedures, index=default_index)

    st.markdown(f"## {selected_proc} のチェックリスト")

    content = checklists.get(selected_proc)

    # --- チェックボックス表示とセッションステートによる状態保持 ---

    if content:
        # セッションステートの初期化
        if 'checklist_states' not in st.session_state:
            st.session_state['checklist_states'] = {}
            
        if selected_proc not in st.session_state['checklist_states']:
            st.session_state['checklist_states'][selected_proc] = {}

        # 項目を解析するための変数
        lines = content.split('\n')
        item_count = 0
        
        # チェック項目の総数とチェック済みの項目の数をカウント
        total_items = 0
        checked_items = 0
        
        # チェックリストの表示と処理
        for line in lines:
            line = line.strip()

            # 1. 見出しの処理 (H3/H4)
            if line.startswith("### "):
                current_section = line.replace("### ", "--- \n**") + "**"
                st.markdown(current_section)
                continue
            if line.startswith("#### "):
                st.markdown(line)
                continue
                
            # 2. チェック項目 (リスト形式: - ✅ または - □) の処理
            if line.startswith("- ✅ ") or line.startswith("- □ "):
                # チェック項目のテキストを抽出
                item_text = line.replace("- ✅ ", "").replace("- □ ", "").strip()
                
                # ユニークなキーを生成 (処置名_セクション名_インデックス)
                checkbox_key = f"chk_{selected_proc}_{item_count}"
                total_items += 1

                # st.checkboxを使用してチェックリストとして表示
                # valueはセッションステートから取得。存在しない場合はFalse (未チェック)
                is_checked = st.session_state['checklist_states'][selected_proc].get(checkbox_key, False)
                
                # チェックボックスを表示。keyを指定することで状態を保持
                new_state = st.checkbox(item_text, value=is_checked, key=checkbox_key)
                
                # 状態が変化した場合、セッションステートを更新 (このロジックは冗長ですが、明示的に記述することで動作を保証)
                if new_state != is_checked:
                    st.session_state['checklist_states'][selected_proc][checkbox_key] = new_state
                    
                if new_state:
                    checked_items += 1
                    
                item_count += 1
            
            # 3. その他の行（原因のリスト項目など）の処理
            elif line:
                st.markdown(line)
        
        # 進捗バーの表示
        if total_items > 0:
            progress_ratio = checked_items / total_items
            st.progress(progress_ratio, text=f"**進捗状況: {checked_items} / {total_items} 項目完了**")
        else:
            st.info("このチェックリストにはチェック項目がありません。")

        # 処置が完了したらチェック状態をリセットするボタン
        if st.button("この処置のチェック状態をリセット"):
            if selected_proc in st.session_state['checklist_states']:
                st.session_state['checklist_states'][selected_proc] = {}
                st.rerun() # リセット後、画面を再描画してチェックボックスを未チェックにする
            
    # --- チェックボックス表示とセッションステートによる状態保持の終わり ---
    
    else:
        # データがない場合の既存ロジック
        standard = STANDARD_CHECKLIST_ITEMS.get(selected_proc)
        if standard:
            st.warning("⚠️ 有効なデータがありません。標準手順を表示します。")
            dummy_content = f"### 【標準安全手順（{selected_proc}）】\n" + "\n".join([f"- ✅ {p}" for p in standard])
            # このダミーコンテンツもst.checkboxとして処理する方が親切ですが、今回はデータがない場合の暫定表示としてmarkdownのままにします。
            st.markdown(dummy_content)
        else:
            st.info("有効なデータがありません。サイドバーの「データ管理・更新」からデータを取得するか、PDFをアップロードしてください。")


def page_manager():
    st.title("⚙️ データ管理・更新")

    st.subheader("1. システムの初期化（Webデータ取得）")
    st.caption("Web上のヒヤリ・ハット報告書(PDF)を解析し、チェックリストを自動生成します。")

    limit = st.number_input("解析するPDF数 (多いと時間がかかります)", 1, 50, 5)

    if st.button("🔄 システムを完全リセットして再構築"):
        with st.spinner("データを削除し、Webから再取得中..."):
            st.cache_data.clear()
            incidents = reset_system(limit)

        clean_incidents_count = len([i for i in incidents if not is_likely_garbled(i.get("description", ""))])
        st.success(
            f"完了しました。全 {len(incidents)} 件のデータを取得し、うち {clean_incidents_count} 件が有効な事例として解析されました。")
        st.info("左のメニューから「チェックリストビューア」へ移動して確認してください。")

    st.markdown("---")

    st.subheader("2. PDFファイルをアップロードしてデータセットに追加")
    st.caption("お手元のインシデント報告書PDFを直接解析し、データセットに追加します。")

    uploaded_file = st.file_uploader("インシデント報告書 (PDF)", type="pdf")

    if uploaded_file is not None:
        if st.button("📄 アップロードされたPDFを解析"):
            with st.spinner("PDFを解析中..."):
                try:
                    pdf_bytes = uploaded_file.read()
                    raw_text = extract_text_from_pdf(pdf_bytes)

                    if len(raw_text) > 100 and not is_likely_garbled(raw_text):
                        record = parse_report_text(raw_text, f"アップロードファイル: {uploaded_file.name}")

                        current = load_data()
                        current.append(record)
                        save_data(current)
                        run_checklist_generation(current)

                        st.success(f"PDFファイル「{uploaded_file.name}」の解析に成功し、データセットが更新されました。")
                        st.markdown(f"**解析結果概要:** {record['description']}")
                        st.info("左のメニューから「チェックリストビューア」へ移動して確認してください。")
                    else:
                        st.error("エラー: PDFから有効な日本語テキストを抽出できませんでした。ファイルが暗号化されているか、文字化けが激しい可能性があります。")
                except Exception as e:
                    st.error(f"解析中に予期せぬエラーが発生しました: {e}")

    st.markdown("---")

    st.subheader("3. 手動インシデント追加")
    st.caption("院内で発生した独自の事例を手動で入力し、チェックリストをアップデートします。")

    with st.form("manual_add"):
        # STANDARD_CHECKLIST_ITEMSのキーを処置種類として使用
        m_proc_options = sorted(list(STANDARD_CHECKLIST_ITEMS.keys()) + ["その他"])
        m_proc = st.selectbox("処置種類", m_proc_options)
        m_desc = st.text_area("インシデント概要", placeholder="例：輸血時に患者IDの確認を省略しそうになった")
        m_cause = st.text_area("原因", placeholder="例：急いでいたため、ダブルチェックが形式的になっていた")
        m_prev = st.text_area("再発防止策・教訓", placeholder="例：指差し呼称を必須とする")

        if st.form_submit_button("リストに追加"):
            new_record = {
                "incident_type": m_proc,
                "description": m_desc,
                "cause": m_cause,
                "prevention": m_prev,
                "source": "手動入力",
                "date": datetime.now().strftime("2025-12-01")
            }
            current = load_data()
            current.append(new_record)
            save_data(current)
            run_checklist_generation(current)
            st.success("追加しました！チェックリストが更新されました。")

    st.markdown("---")
    st.subheader("現在のデータセット概要 (最新10件)")
    incidents = load_data()

    clean_incidents = [i for i in incidents if not is_likely_garbled(i.get("description", ""))]

    if clean_incidents:
        df = pd.DataFrame([
            {"種別": i.get("incident_type"),
             "概要": i.get("description", "").replace('\n', ' ')[:40] + "...",
             "原因": i.get("cause", "").replace('\n', ' ')[:40] + "..."
             }
            for i in clean_incidents[-10:]
        ])
        st.caption(f"全データ件数: {len(incidents)}件 (うち、文字化けを除外した有効件数: {len(clean_incidents)}件)")
        st.table(df)
    else:
        st.write("有効なデータがありません。PDFのアップロードまたは手動入力を試してください。")


# ==========================================
# 4. メイン実行部
# ==========================================
def main():
    st.set_page_config(page_title="医療安全AI", layout="wide")
    
    # ★★★ 最終強制リセットロジック ★★★
    if os.path.exists(CHECKLISTS_PATH) and not st.session_state.get('initial_load_done', False):
        try:
            st.session_state['initial_load_done'] = True
            
            with open(CHECKLISTS_PATH, 'r', encoding='utf-8') as f:
                content = json.load(f)
                # データのサイズが非常に小さい場合は、古いデータ構造の可能性があるため再構築
                if len(content.get('輸血', '')) < 100:
                    st.warning("🔄 古いチェックリストデータが検出されました。最新のコードでリストを再生成します。")
                    if os.path.exists(DATASET_PATH):  
                        incidents = load_data()
                        run_checklist_generation(incidents)
                    else:
                        run_checklist_generation([])

        except (json.JSONDecodeError, FileNotFoundError):
            if os.path.exists(DATASET_PATH):  
                incidents = load_data()
                run_checklist_generation(incidents)
            else:
                run_checklist_generation([])

    st.sidebar.title("メニュー")
    page = st.sidebar.radio("機能選択", ["チェックリストビューア", "データ管理・更新"])

    if page == "チェックリストビューア":
        page_viewer()
    elif page == "データ管理・更新":
        page_manager()


if __name__ == "__main__":
    main()
