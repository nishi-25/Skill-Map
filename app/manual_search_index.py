# ─── マニュアルのページ構成・検索インデックス ──────────────────────────
# このリストがサイドバーTOC・検索・前へ/次へナビの単一のソースになる。
# min_role: None=誰でも見える / "manager"=Manager・Admin向けにナビ表示 / "admin"=Admin専用(ルートも auth.require_admin でガード)

PAGES = [
    {"key": "index", "url": "/manual", "title": "システム概要", "icon": "bi-map", "group": "はじめに", "min_role": None},
    {"key": "quickstart", "url": "/manual/quickstart", "title": "クイックスタート", "icon": "bi-play-circle-fill", "group": "はじめに", "min_role": None},
    {"key": "roles", "url": "/manual/roles", "title": "ロール・権限一覧", "icon": "bi-person-badge", "group": "はじめに", "min_role": None},
    {"key": "login", "url": "/manual/login", "title": "ログイン・初期設定", "icon": "bi-box-arrow-in-right", "group": "はじめに", "min_role": None},

    {"key": "dashboard", "url": "/manual/dashboard", "title": "ダッシュボード", "icon": "bi-speedometer2", "group": "全ユーザー共通", "min_role": None},
    {"key": "profile", "url": "/manual/profile", "title": "プロフィール・表示設定", "icon": "bi-person-circle", "group": "全ユーザー共通", "min_role": None},
    {"key": "search", "url": "/manual/search", "title": "グローバル検索", "icon": "bi-search", "group": "全ユーザー共通", "min_role": None},
    {"key": "tickets", "url": "/manual/tickets", "title": "問い合わせ・要望", "icon": "bi-chat-dots-fill", "group": "全ユーザー共通", "min_role": None},
    {"key": "wiki", "url": "/manual/wiki", "title": "Wiki", "icon": "bi-journal-text", "group": "全ユーザー共通", "min_role": None},

    {"key": "skillmap", "url": "/manual/skillmap", "title": "スキルマップ（自己申告）", "icon": "bi-lightning-charge-fill", "group": "User機能", "min_role": None},
    {"key": "business-map", "url": "/manual/business-map", "title": "業務マップで選択する", "icon": "bi-diagram-3", "group": "User機能", "min_role": None},
    {"key": "my-approvals", "url": "/manual/my-approvals", "title": "自分の申請状況", "icon": "bi-send", "group": "User機能", "min_role": None},
    {"key": "timeline", "url": "/manual/timeline", "title": "成長タイムライン", "icon": "bi-graph-up-arrow", "group": "User機能", "min_role": None},
    {"key": "education", "url": "/manual/education", "title": "教育リソース", "icon": "bi-mortarboard-fill", "group": "User機能", "min_role": None},
    {"key": "certifications", "url": "/manual/certifications", "title": "資格登録", "icon": "bi-patch-check-fill", "group": "User機能", "min_role": None},
    {"key": "exams-my", "url": "/manual/exams-my", "title": "試験を受ける", "icon": "bi-pencil-square", "group": "User機能", "min_role": None},
    {"key": "annual-plan", "url": "/manual/annual-plan", "title": "年間計画", "icon": "bi-calendar-check", "group": "User機能", "min_role": None},

    {"key": "annual-plan-team", "url": "/manual/annual-plan-team", "title": "チームの年間計画", "icon": "bi-people", "group": "Manager以上", "min_role": "manager"},
    {"key": "approvals", "url": "/manual/approvals", "title": "承認依頼", "icon": "bi-check2-square", "group": "Manager以上", "min_role": "manager"},
    {"key": "matrix", "url": "/manual/matrix", "title": "スキルマトリクス", "icon": "bi-grid-3x3-gap", "group": "Manager以上", "min_role": "manager"},
    {"key": "catalog", "url": "/manual/catalog", "title": "スキルカタログ管理", "icon": "bi-collection", "group": "Manager以上", "min_role": "manager"},
    {"key": "categories", "url": "/manual/categories", "title": "カテゴリー管理", "icon": "bi-tags", "group": "Manager以上", "min_role": "manager"},
    {"key": "groups", "url": "/manual/groups", "title": "グループ管理", "icon": "bi-people-fill", "group": "Manager以上", "min_role": "manager"},
    {"key": "education-mgmt", "url": "/manual/education-mgmt", "title": "教育リソース管理", "icon": "bi-mortarboard-fill", "group": "Manager以上", "min_role": "manager"},
    {"key": "business-map-manage", "url": "/manual/business-map-manage", "title": "業務マップ管理", "icon": "bi-diagram-3-fill", "group": "Manager以上", "min_role": "manager"},
    {"key": "certifications-matrix", "url": "/manual/certifications-matrix", "title": "資格マトリクス", "icon": "bi-patch-check", "group": "Manager以上", "min_role": "manager"},
    {"key": "certifications-catalog", "url": "/manual/certifications-catalog", "title": "資格マスタ管理", "icon": "bi-collection-fill", "group": "Manager以上", "min_role": "manager"},
    {"key": "exams-management", "url": "/manual/exams-management", "title": "試験管理", "icon": "bi-clipboard-check", "group": "Manager以上", "min_role": "manager"},

    {"key": "usecase-user", "url": "/manual/usecase/user", "title": "ユースケース: Userの操作フロー", "icon": "bi-person-walking", "group": "ユースケース", "min_role": None},
    {"key": "usecase-manager", "url": "/manual/usecase/manager", "title": "ユースケース: Managerの操作フロー", "icon": "bi-person-gear", "group": "ユースケース", "min_role": None},

    {"key": "api", "url": "/manual/api", "title": "APIリファレンス", "icon": "bi-code-slash", "group": "APIリファレンス", "min_role": None},

    {"key": "faq", "url": "/manual/faq", "title": "よくある質問", "icon": "bi-question-circle", "group": "その他", "min_role": None},

    {"key": "admin-index", "url": "/manual/admin", "title": "管理者の役割", "icon": "bi-shield-check", "group": "Admin専用", "min_role": "admin"},
    {"key": "admin-users", "url": "/manual/admin/users", "title": "ユーザー管理・パスワードリセット", "icon": "bi-people", "group": "Admin専用", "min_role": "admin"},
    {"key": "admin-mail", "url": "/manual/admin/mail", "title": "メール設定", "icon": "bi-envelope-at", "group": "Admin専用", "min_role": "admin"},
    {"key": "admin-maintenance", "url": "/manual/admin/maintenance", "title": "運用・保守（Docker / 初期化 / トラブルシューティング）", "icon": "bi-tools", "group": "Admin専用", "min_role": "admin"},
]

PAGES_BY_KEY = {p["key"]: p for p in PAGES}


def get_prev_next(key: str):
    """同じグループ内に限らず PAGES の並び順で前後ページを返す"""
    idx = next((i for i, p in enumerate(PAGES) if p["key"] == key), None)
    if idx is None:
        return None, None
    prev_page = PAGES[idx - 1] if idx > 0 else None
    next_page = PAGES[idx + 1] if idx < len(PAGES) - 1 else None
    return prev_page, next_page


def build_toc_groups(current_user):
    """current_user のロールに応じてサイドバーTOCのグループ一覧を構築する"""
    role = current_user.role if current_user else None
    groups = []
    group_index = {}
    for p in PAGES:
        if p["min_role"] == "manager" and role not in ("admin", "manager"):
            continue
        if p["min_role"] == "admin" and role != "admin":
            continue
        if p["group"] not in group_index:
            group_index[p["group"]] = {"label": p["group"], "pages": []}
            groups.append(group_index[p["group"]])
        group_index[p["group"]]["pages"].append(p)
    return groups


# ─── 検索インデックス ──────────────────────────────────────────────
# 各ページのセクション単位で {page_url, anchor, title, breadcrumb, keywords, snippet} を持つ。
MANUAL_INDEX = [
    {"page_url": "/manual", "anchor": "overview", "title": "システム概要", "breadcrumb": "はじめに",
     "keywords": "スキルマップ 概要 自己申告 承認 マトリクス グループ 教育リソース 問い合わせ",
     "snippet": "チームメンバーのスキルレベルを記録・可視化・管理するアプリです。"},
    {"page_url": "/manual/quickstart", "anchor": "quickstart", "title": "クイックスタート", "breadcrumb": "はじめに",
     "keywords": "ガイドツアー 初回ログイン チュートリアル ツアー",
     "snippet": "初回ログイン後にロール別のガイドツアーが自動起動します。サイドバー下部からいつでも再実行できます。"},
    {"page_url": "/manual/roles", "anchor": "roles", "title": "ロール・権限一覧", "breadcrumb": "はじめに",
     "keywords": "ロール 権限 User Manager Admin 権限一覧 アクセス制御",
     "snippet": "User / Manager / Admin の3ロールごとに利用できる機能を一覧で確認できます。"},
    {"page_url": "/manual/login", "anchor": "new-registration", "title": "新規登録", "breadcrumb": "はじめに › ログイン・初期設定",
     "keywords": "登録 サインアップ 承認待ち pending 新規ユーザー デモ",
     "snippet": "新規登録後はAdminの承認待ち状態になります。承認されるまでログインできません。"},
    {"page_url": "/manual/login", "anchor": "forgot-password", "title": "パスワードを忘れた場合", "breadcrumb": "はじめに › ログイン・初期設定",
     "keywords": "パスワード 再設定 リセット メール 忘れた",
     "snippet": "登録済みメールアドレスを入力するとリセット用リンクが届きます。"},

    {"page_url": "/manual/dashboard", "anchor": "filter", "title": "ダッシュボードのフィルター", "breadcrumb": "全ユーザー共通 › ダッシュボード",
     "keywords": "ダッシュボード フィルター ユーザー グループ 絞り込み URL",
     "snippet": "上部のドロップダウンでユーザーまたはグループを選択して絞り込めます。"},
    {"page_url": "/manual/dashboard", "anchor": "widgets", "title": "ダッシュボードの表示内容", "breadcrumb": "全ユーザー共通 › ダッシュボード",
     "keywords": "統計カード グループ別サマリー ティア別進捗 成長トレンド ランキング CSVエクスポート",
     "snippet": "統計カード・グループ別サマリー・ティア別進捗・成長トレンド・メンバーランキングなどを表示します。"},
    {"page_url": "/manual/profile", "anchor": "avatar", "title": "アバター設定", "breadcrumb": "全ユーザー共通 › プロフィール",
     "keywords": "アバター 画像 プロフィール写真 アップロード",
     "snippet": "プロフィール画像の右下カメラアイコンから画像（最大2MB）をアップロードできます。"},
    {"page_url": "/manual/profile", "anchor": "display-settings", "title": "表示設定（テーマ・言語・ダークモード）", "breadcrumb": "全ユーザー共通 › プロフィール",
     "keywords": "カラーテーマ 言語 日本語 English ダークモード テーマ",
     "snippet": "カラーテーマ（Warm/Cool）、言語、ダークモードを切り替えられます。設定はブラウザに保存されます。"},
    {"page_url": "/manual/search", "anchor": "global-search", "title": "グローバル検索の使い方", "breadcrumb": "全ユーザー共通",
     "keywords": "検索 サイドバー スキル名 メンバー名 グループ名",
     "snippet": "サイドバー上部の検索バーでスキル名・メンバー名・グループ名を横断検索できます。"},
    {"page_url": "/manual/tickets", "anchor": "new-ticket", "title": "問い合わせの新規作成", "breadcrumb": "全ユーザー共通 › 問い合わせ・要望",
     "keywords": "問い合わせ 要望 チャット サポート 送信",
     "snippet": "画面右下のチャットアイコンから新しい問い合わせ・要望を送信できます。"},
    {"page_url": "/manual/tickets", "anchor": "admin-response", "title": "問い合わせへの対応（Admin）", "breadcrumb": "全ユーザー共通 › 問い合わせ・要望",
     "keywords": "Admin 対応 未対応 ステータス 解決済み クローズ",
     "snippet": "Adminは問い合わせ・要望ページで全チケットを管理し、ステータスを更新できます。"},
    {"page_url": "/manual/tickets", "anchor": "feature-requests", "title": "要望の管理", "breadcrumb": "全ユーザー共通 › 問い合わせ・要望",
     "keywords": "要望リスト 優先度 ピン留め",
     "snippet": "種別を「要望」にすると優先度順に要望リストへピン留め表示されます。"},
    {"page_url": "/manual/wiki", "anchor": "wiki-create", "title": "Wikiページの作成", "breadcrumb": "全ユーザー共通 › Wiki",
     "keywords": "Wiki メモ Markdown プレビュー 画像 公開範囲",
     "snippet": "Markdownでページを作成できます。公開範囲は個人メモ／全体／グループから選べます。"},
    {"page_url": "/manual/wiki", "anchor": "wiki-permission", "title": "Wikiの編集・削除権限", "breadcrumb": "全ユーザー共通 › Wiki",
     "keywords": "Wiki 編集権限 削除権限 作成者 共同編集",
     "snippet": "編集は作成者・Admin、または「メンバーも編集可」に設定された共有ページのみ。削除は作成者・Adminのみです。"},

    {"page_url": "/manual/skillmap", "anchor": "declare-steps", "title": "スキルの申告手順", "breadcrumb": "User機能 › スキルマップ（自己申告）",
     "keywords": "申告 サブスキル チェックリスト レベル 自己評価 承認者 根拠 エビデンス 上書き",
     "snippet": "サブスキルにチェックすると自動でレベルが算出されます。必要なら理由付きで上書きし、承認者を選んで申請します。"},
    {"page_url": "/manual/skillmap", "anchor": "level-definition", "title": "スキルレベルの定義（0〜4）", "breadcrumb": "User機能 › スキルマップ（自己申告）",
     "keywords": "レベル 未経験 入門 実務可 指導可 エキスパート 0 1 2 3 4",
     "snippet": "0:未経験 / 1:入門 / 2:実務可 / 3:指導可 / 4:エキスパートの5段階です。"},
    {"page_url": "/manual/skillmap", "anchor": "entry", "title": "スキルマップの入口の選び方", "breadcrumb": "User機能 › スキルマップ（自己申告）",
     "keywords": "入口 直接選択 業務マップ start",
     "snippet": "「直接スキルを選択」と「業務マップから選択」のどちらでも同じデータ・同じ承認フローになります。"},
    {"page_url": "/manual/skillmap", "anchor": "revoke", "title": "承認済みスキルの取り消し", "breadcrumb": "User機能 › スキルマップ（自己申告）",
     "keywords": "取消申請 取り消し 解除 レベルを下げる revoke_pending",
     "snippet": "承認済みスキルは自分でチェックを外せません。「取り消し申請をする」から申請し、承認されると未経験(Lv0)に戻ります。"},
    {"page_url": "/manual/business-map", "anchor": "business-map-start", "title": "スキルマップの入口（/skills/start）", "breadcrumb": "User機能 › 業務マップで選択する",
     "keywords": "スキルマップ 入口 直接選択 業務マップ start",
     "snippet": "「スキルマップ」をクリックすると、直接スキルを選ぶか業務マップから選ぶかを選択できます。"},
    {"page_url": "/manual/business-map", "anchor": "business-map-declare", "title": "業務マップからの申告", "breadcrumb": "User機能 › 業務マップで選択する",
     "keywords": "業務マップ エリア サブスキル 申告 ツリー",
     "snippet": "業務エリアのツリーから該当するサブスキルにチェックして、エリア単位・スキル単位で申告できます。"},
    {"page_url": "/manual/business-map", "anchor": "business-map-views", "title": "表示の切り替え（リスト・マインドマップ・ブロック）", "breadcrumb": "User機能 › 業務マップで選択する",
     "keywords": "業務マップ リスト表示 マインドマップ ブロック表示 タブ 進捗バー",
     "snippet": "リスト・マインドマップ・ブロックの3表示を切り替えられます。ブロック表示はカードのグリッドで進捗を一覧できます。"},
    {"page_url": "/manual/my-approvals", "anchor": "status", "title": "自分の申請状況", "breadcrumb": "User機能",
     "keywords": "承認待ち 承認済み 差し戻し 再申請",
     "snippet": "申告したスキルの承認状況（承認待ち・承認済み・差し戻し）を確認できます。"},
    {"page_url": "/manual/timeline", "anchor": "timeline", "title": "成長タイムライン", "breadcrumb": "User機能",
     "keywords": "成長 タイムライン グラフ 履歴 レベルアップ",
     "snippet": "承認されたスキルの変化を時系列グラフで確認できます。"},
    {"page_url": "/manual/education", "anchor": "education", "title": "教育リソースの閲覧", "breadcrumb": "User機能",
     "keywords": "教育リソース 学習 リンク集 教材 カテゴリー",
     "snippet": "Manager/Adminが登録した学習サイト・教材へのリンク集です。"},
    {"page_url": "/manual/certifications", "anchor": "cert-register", "title": "資格の登録", "breadcrumb": "User機能 › 資格登録",
     "keywords": "資格 認定 登録 発行日 有効期限 証明書番号 スコア エビデンス",
     "snippet": "マスタカタログから選択、または自由入力で資格を登録できます。証明ファイルの添付も可能です。"},
    {"page_url": "/manual/certifications", "anchor": "cert-edit", "title": "資格の編集・削除", "breadcrumb": "User機能 › 資格登録",
     "keywords": "資格 編集 削除 自分のみ",
     "snippet": "登録した資格の編集・削除ができるのは本人のみです（Manager/Adminも他人の資格は編集できません）。"},
    {"page_url": "/manual/exams-my", "anchor": "exam-take", "title": "試験を受ける（学科・実技）", "breadcrumb": "User機能 › 試験を受ける",
     "keywords": "試験 学科 実技 選択問題 エビデンス 提出 採点",
     "snippet": "学科は選択問題に回答して自動採点、実技はエビデンスファイルを提出してManagerが採点します。"},
    {"page_url": "/manual/annual-plan", "anchor": "register", "title": "年間計画の登録方法（ドラッグ&ドロップ）", "breadcrumb": "User機能 › 年間計画",
     "keywords": "年間計画 ドラッグ ドロップ カレンダー サブスキル 業務エリア 資格 試験 月 週 日 年",
     "snippet": "左の一覧からスキル・サブスキル・業務エリア・資格・試験をカレンダーへドラッグ&ドロップして計画を登録できます。"},
    {"page_url": "/manual/annual-plan", "anchor": "achievement", "title": "年間計画の達成判定ルール", "breadcrumb": "User機能 › 年間計画",
     "keywords": "年間計画 達成 未達成 期限切れ 目標日 資格 試験",
     "snippet": "資格・試験は目標日までに取得・合格していないと未達成になります（サブスキル等は期日を問いません）。"},

    {"page_url": "/manual/annual-plan-team", "anchor": "cards", "title": "チームメンバーの期限切れ・未達成計画を確認", "breadcrumb": "Manager以上 › チームの年間計画",
     "keywords": "チーム 年間計画 期限切れ 未達成 遅れ フォロー マネージャー",
     "snippet": "担当グループのメンバーの年間計画のうち、目標日を過ぎても達成できていない計画だけを一覧で確認できます。"},

    {"page_url": "/manual/approvals", "anchor": "single-approval", "title": "個別承認・差し戻し", "breadcrumb": "Manager以上 › 承認依頼",
     "keywords": "承認 差し戻し コメント 却下",
     "snippet": "「承認」または「差し戻し」ボタンでコメント付きで申告をレビューできます。"},
    {"page_url": "/manual/approvals", "anchor": "bulk-approval", "title": "一括承認・差し戻し", "breadcrumb": "Manager以上 › 承認依頼",
     "keywords": "一括承認 一括差し戻し チェックボックス 全選択",
     "snippet": "複数選択して「選択を承認」「選択を差し戻し」を一括実行できます。"},
    {"page_url": "/manual/approvals", "anchor": "revoke-approval", "title": "取り消し申請の承認", "breadcrumb": "Manager以上 › 承認依頼",
     "keywords": "取消申請 承認 却下 revoke_pending レベルを下げる",
     "snippet": "「取消を承認」でLv0に確定、「却下」で取り消し申請前のレベルを維持します。"},
    {"page_url": "/manual/approvals", "anchor": "admin-scope", "title": "承認の対象範囲（Admin/Manager）", "breadcrumb": "Manager以上 › 承認依頼",
     "keywords": "Admin 全員 Manager 担当グループ 自動承認",
     "snippet": "Managerは担当グループのみ、Adminは全員の申告を承認できます。Admin/Manager自身の申告は自動承認されます。"},
    {"page_url": "/manual/matrix", "anchor": "tabs", "title": "スキルマトリクスのタブ（スキル別／業務別）", "breadcrumb": "Manager以上 › スキルマトリクス",
     "keywords": "タブ 業務別 スキル別 業務マップ 切り替え",
     "snippet": "「スキル別」と「業務マップのエリア単位で見る「業務別」タブを切り替えられます。"},
    {"page_url": "/manual/matrix", "anchor": "heatmap", "title": "スキルマトリクスのヒートマップ", "breadcrumb": "Manager以上 › スキルマトリクス",
     "keywords": "マトリクス ヒートマップ 理解度 色",
     "snippet": "メンバー×スキルのヒートマップで理解度を暖色グラデーションで可視化します。"},
    {"page_url": "/manual/matrix", "anchor": "charts", "title": "スキルマトリクスの分析チャート", "breadcrumb": "Manager以上 › スキルマトリクス",
     "keywords": "成長トレンド レベル分布 レーダー ユーザー別平均",
     "snippet": "成長トレンド・レベル分布・カテゴリー別レーダー・ユーザー別平均などのグラフを表示します。"},
    {"page_url": "/manual/catalog", "anchor": "add-skill", "title": "スキルの追加", "breadcrumb": "Manager以上 › スキルカタログ管理",
     "keywords": "スキル追加 カテゴリー ティア 難易度",
     "snippet": "スキル名・カテゴリー・難易度ティア・説明を入力してスキルを追加します。"},
    {"page_url": "/manual/catalog", "anchor": "bulk-import", "title": "一括インポート（CSV/Excel/Markdown/JSON）", "breadcrumb": "Manager以上 › スキルカタログ管理",
     "keywords": "CSV Excel Markdown JSON インポート テンプレート",
     "snippet": "テンプレートをダウンロードして編集後、4形式（CSV/Excel/Markdown/JSON）でアップロードできます。"},
    {"page_url": "/manual/categories", "anchor": "categories", "title": "カテゴリー管理", "breadcrumb": "Manager以上",
     "keywords": "カテゴリー 追加 編集 削除 カードビュー リストビュー",
     "snippet": "スキルを分類するカテゴリーの追加・編集・削除ができます。"},
    {"page_url": "/manual/groups", "anchor": "create-group", "title": "グループ作成", "breadcrumb": "Manager以上 › グループ管理",
     "keywords": "グループ作成 担当Manager 親グループ 階層 必須スキル ギャップ分析",
     "snippet": "担当Managerの設定、親グループによる階層化、必須スキル割当によるギャップ分析ができます。"},
    {"page_url": "/manual/groups", "anchor": "manage-members", "title": "グループのメンバー管理", "breadcrumb": "Manager以上 › グループ管理",
     "keywords": "メンバー追加 メンバー削除 一括追加",
     "snippet": "グループカードの「メンバー管理」から複数メンバーを一括追加できます。"},
    {"page_url": "/manual/education-mgmt", "anchor": "education-mgmt", "title": "教育リソースの登録", "breadcrumb": "Manager以上",
     "keywords": "教育リソース管理 リンク追加 ファビコン 関連スキル",
     "snippet": "タイトル・URL・関連スキルを設定して学習リンクを登録できます。"},
    {"page_url": "/manual/business-map-manage", "anchor": "business-map-areas", "title": "業務エリアの作成・管理", "breadcrumb": "Manager以上 › 業務マップ管理",
     "keywords": "業務マップ エリア 階層 ドラッグ ドロップ 並び替え スキル割当",
     "snippet": "業務エリアを階層的に作成し、ドラッグ＆ドロップでサブスキルを割り当てられます。"},
    {"page_url": "/manual/business-map-manage", "anchor": "business-map-import", "title": "業務マップのインポート・エクスポート", "breadcrumb": "Manager以上 › 業務マップ管理",
     "keywords": "業務マップ インポート エクスポート JSON Excel CSV テンプレート",
     "snippet": "JSON/Excel/CSVでエリア構成を一括インポート・エクスポートできます。"},
    {"page_url": "/manual/certifications-matrix", "anchor": "cert-matrix", "title": "資格マトリクスの見方", "breadcrumb": "Manager以上 › 資格マトリクス",
     "keywords": "資格マトリクス 保有者数 グラフ メンバー",
     "snippet": "資格ごとの保有者数や、メンバーごとの取得資格数を集計グラフで確認できます。"},
    {"page_url": "/manual/certifications-catalog", "anchor": "cert-catalog", "title": "資格マスタの管理", "breadcrumb": "Manager以上 › 資格マスタ管理",
     "keywords": "資格マスタ カタログ ティア tier アーカイブ インポート エクスポート",
     "snippet": "資格名・発行団体・ティア（難易度）を設定してマスタを管理します。エクスポート/インポートはAdminのみ。"},
    {"page_url": "/manual/exams-management", "anchor": "exam-create", "title": "試験の作成（学科・実技）", "breadcrumb": "Manager以上 › 試験管理",
     "keywords": "試験作成 問題 選択問題 採点基準 合格点 受験資格",
     "snippet": "選択問題（学科）と採点基準（実技）を設定して試験を作成できます。受験にスキル習熟度の条件を付けることもできます。"},
    {"page_url": "/manual/exams-management", "anchor": "exam-assign-grade", "title": "試験の割り当て・採点", "breadcrumb": "Manager以上 › 試験管理",
     "keywords": "試験 割り当て 採点 結果 エビデンス確認",
     "snippet": "対象ユーザーに試験を割り当て、提出されたエビデンスを確認して採点します。"},

    {"page_url": "/manual/usecase/user", "anchor": "usecase-user", "title": "Userの操作フロー（一連の使い方）", "breadcrumb": "ユースケース",
     "keywords": "ユーザー 操作フロー 自己申告 一連の流れ ウォークスルー",
     "snippet": "ログインからスキル申告、承認状況の確認までの一連の流れをステップ形式で説明します。"},
    {"page_url": "/manual/usecase/manager", "anchor": "usecase-manager", "title": "Managerの操作フロー（一連の使い方）", "breadcrumb": "ユースケース",
     "keywords": "マネージャー 操作フロー 承認 マトリクス 一連の流れ",
     "snippet": "ログインからチーム確認、承認、スキルマトリクス分析までの一連の流れを説明します。"},

    {"page_url": "/manual/api", "anchor": "auth-model", "title": "API認証モデル（セッションCookie）", "breadcrumb": "APIリファレンス",
     "keywords": "API 認証 cookie session requests login トークン APIキー",
     "snippet": "全APIはブラウザのセッションCookieで認証されます。APIキーやトークンはありません。"},
    {"page_url": "/manual/api", "anchor": "tag-skills", "title": "Skills API（スキルレベル登録など）", "breadcrumb": "APIリファレンス",
     "keywords": "API スキル レベル 登録 Python requests POST",
     "snippet": "/api/skills/{id}/level など、スキル関連のJSON APIとPythonサンプル。"},
    {"page_url": "/manual/api", "anchor": "try-it-out", "title": "動作確認（Swagger UI / Try it out）", "breadcrumb": "APIリファレンス",
     "keywords": "Swagger docs 動作確認 Try it out Execute FastAPI",
     "snippet": "「ここで試す」リンクから/docsのSwagger UIを開き、ログイン済みのまま実際にAPIを実行できます。"},

    {"page_url": "/manual/faq", "anchor": "faq", "title": "よくある質問", "breadcrumb": "その他",
     "keywords": "FAQ 質問 承認されない 差し戻し グループ チャット 赤い",
     "snippet": "申告が承認されない、差し戻された、グループに追加されない等のよくある質問への回答です。"},

    {"page_url": "/manual/admin", "anchor": "admin-overview", "title": "管理者の役割", "breadcrumb": "Admin専用",
     "keywords": "Admin Manager 役割 比較 権限",
     "snippet": "AdminとManagerの権限の違いを比較表で確認できます。"},
    {"page_url": "/manual/admin/users", "anchor": "approve-users", "title": "新規ユーザーの承認", "breadcrumb": "Admin専用 › ユーザー管理",
     "keywords": "ユーザー承認 未承認 ロール変更",
     "snippet": "未承認ユーザーの承認、user/manager/adminのロール変更ができます。"},
    {"page_url": "/manual/admin/users", "anchor": "password-reset-docker", "title": "パスワードの手動リセット（Dockerコマンド）", "breadcrumb": "Admin専用 › ユーザー管理",
     "keywords": "パスワードリセット docker exec python コマンド",
     "snippet": "メールが使えない場合、Dockerコンテナ内でPythonスクリプトを実行してパスワードをリセットします。"},
    {"page_url": "/manual/admin/mail", "anchor": "smtp-settings", "title": "メール設定（SMTP）", "breadcrumb": "Admin専用",
     "keywords": "SMTP メール 設定 Gmail アプリパスワード ポート",
     "snippet": "SMTPホスト・ポート・ユーザー名・パスワードを設定するとメール通知が送信されます。"},
    {"page_url": "/manual/admin/maintenance", "anchor": "docker-ops", "title": "Docker運用コマンド", "breadcrumb": "Admin専用 › 運用・保守",
     "keywords": "docker compose up down restart logs バックアップ 復元",
     "snippet": "起動・停止・再起動・ログ確認・バックアップ等の基本Dockerコマンド一覧です。"},
    {"page_url": "/manual/admin/maintenance", "anchor": "catalog-init", "title": "カタログ初期化", "breadcrumb": "Admin専用 › 運用・保守",
     "keywords": "カタログ初期化 デフォルト リセット",
     "snippet": "現在のカテゴリ・スキル・申告データを削除し、デフォルトカタログに戻します。"},
    {"page_url": "/manual/admin/maintenance", "anchor": "troubleshooting", "title": "トラブルシューティング", "breadcrumb": "Admin専用 › 運用・保守",
     "keywords": "トラブル 起動しない メール届かない マイグレーション エラー",
     "snippet": "コンテナが起動しない、メールが届かない等のよくある問題と対処法です。"},
]


def search_manual(q: str, limit: int = 15):
    q = (q or "").strip().lower()
    if not q:
        return []
    scored = []
    for entry in MANUAL_INDEX:
        haystacks = [
            (entry["title"], 3),
            (entry["keywords"], 2),
            (entry["breadcrumb"], 1),
            (entry["snippet"], 1),
        ]
        score = sum(weight for text, weight in haystacks if q in text.lower())
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:limit]]
