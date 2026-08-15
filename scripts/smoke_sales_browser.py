"""Sales UI journey against a running app. Uses labelled synthetic inputs only."""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    output = Path("test-results/sales-browser")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        project = page.request.post(args.url + "/api/projects", data={"name": "销售浏览器测试 · 合成项目"}).json()
        page.reload(wait_until="networkidle")
        page.get_by_label("当前项目", exact=True).select_option(project["id"])
        page.get_by_role("button", name="销售质检", exact=True).click()
        expect(page.get_by_role("heading", name="销售会话质检", exact=True)).to_be_visible()
        page.get_by_role("button", name="体验销售示例", exact=True).click()
        expect(page.get_by_role("heading", name="客户需求访谈 · 合成演示", exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_role("heading", name="保存主管复核", exact=True)).to_be_visible(timeout=30000)
        page.locator(".sales-decision .text-button").first.click()
        expect(page.get_by_role("complementary", name="销售原文依据")).to_be_visible()
        page.screenshot(path=str(output / "01-evidence.png"), full_page=True)
        page.get_by_role("button", name="关闭原文", exact=True).click()
        page.get_by_label("next 主管判断", exact=True).select_option("met")
        page.get_by_label("next 改判原因", exact=True).fill("浏览器测试：主管演示补充确认")
        page.get_by_label("next 创建辅导任务", exact=True).check()
        page.get_by_role("button", name="保存复核结果", exact=True).click()
        expect(page.get_by_text("主管复核已保存", exact=False)).to_be_visible(timeout=30000)
        with page.expect_download() as download:
            page.get_by_role("link", name="导出质检报告", exact=True).click()
        path = output / download.value.suggested_filename
        download.value.save_as(path)
        exported = path.read_text(encoding="utf-8")
        assert "浏览器测试：主管演示补充确认" in exported and "原文依据" in exported
        page.screenshot(path=str(output / "02-reviewed.png"), full_page=True)
        page.get_by_role("button", name="团队概览", exact=True).click()
        page.get_by_label("数据模式", exact=True).select_option("demo")
        page.get_by_role("button", name="应用筛选", exact=True).click()
        expect(page.get_by_role("heading", name="演示销售小林", exact=True)).to_be_visible(timeout=10000)
        page.screenshot(path=str(output / "03-dashboard.png"), full_page=True)
        page.get_by_role("button", name="评分标准", exact=True).click()
        page.get_by_role("button", name="以此为基础修订", exact=True).first.click()
        page.get_by_label("评分卡名称", exact=True).fill("浏览器测试评分卡")
        page.get_by_role("button", name="发布新版本", exact=True).click()
        expect(page.get_by_role("heading", name="浏览器测试评分卡 · v2", exact=True)).to_be_visible(timeout=10000)
        page.get_by_role("button", name="会话与复核", exact=True).click()
        page.get_by_role("button", name="导入销售会话", exact=True).click()
        page.get_by_label("会话标题", exact=True).fill("浏览器导入 · 合成测试")
        page.get_by_label("销售人员", exact=True).fill("浏览器演示销售")
        page.get_by_label("客户 / 商机标识", exact=True).fill("合成客户")
        page.get_by_label("会话文字", exact=True).fill("销售：您目前怎么处理？\n客户：价格比较高。\n销售：下一步安排演示。")
        page.get_by_role("button", name="导入并质检", exact=True).click()
        expect(page.get_by_role("heading", name="浏览器导入 · 合成测试", exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_role("heading", name="保存主管复核", exact=True)).to_be_visible(timeout=30000)
        page.get_by_role("button", name="核对发言身份", exact=True).click()
        page.get_by_label("s1 发言身份", exact=True).select_option("unknown")
        page.get_by_role("button", name="保存身份确认", exact=True).click()
        expect(page.get_by_role("button", name="开始质检", exact=True)).to_be_visible(timeout=10000)
        page.get_by_role("button", name="开始质检", exact=True).click()
        expect(page.get_by_role("heading", name="保存主管复核", exact=True)).to_be_visible(timeout=30000)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "04-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile overflow"
        assert not errors, errors
        page.get_by_role("button", name="任务看板", exact=True).click()
        expect(page.get_by_role("heading", name="销售辅导：确认双方下一步", exact=True)).to_be_visible(timeout=10000)
        browser.close()
        print("Sales browser passed: demo, evidence, review override, task, export, dashboard, card version, text import, speaker revision, mobile.")


if __name__ == "__main__":
    main()
