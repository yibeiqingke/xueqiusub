from app.services.digest import clean_xueqiu_content_for_summary, render_ai_summary_card

def test_clean_xueqiu_content_for_summary_html():
    raw = "<p>这是一段包含<b>HTML标签</b>的动态内容。</p><br/>&nbsp;&nbsp;"
    cleaned = clean_xueqiu_content_for_summary("张三", raw)
    assert "<p>" not in cleaned
    assert "这是一段包含HTML标签的动态内容。" in cleaned

def test_clean_xueqiu_content_for_summary_repost():
    raw = "我的独立思考意见 //@李四: 转发原帖: 市场行情大好 //@张三: 起源贴"
    cleaned = clean_xueqiu_content_for_summary("张三", raw)
    assert "我的独立思考意见" in cleaned
    assert "[引用 @李四:" in cleaned

def test_render_ai_summary_card():
    summary_text = "【核心要点】\n1. 科技股持续走强\n2. 注意仓位风险"
    html = render_ai_summary_card(summary_text)
    assert "AI 智能投研内参" in html
    assert "科技股持续走强" in html
    assert "注意仓位风险" in html
    assert "linear-gradient" in html
