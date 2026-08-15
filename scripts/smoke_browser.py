"""Browser smoke test against a running local app. Creates labelled demo data."""
import argparse
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    output = Path("test-results/browser")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        expect(page.get_by_role("heading", name="每一次讨论，都有下一步。")).to_be_visible()
        page.screenshot(path=str(output / "01-home.png"), full_page=True)
        page.get_by_role("button", name="体验示例", exact=True).click()
        expect(page.get_by_role("heading", name="发布评审 · 演示案例")).to_be_visible(timeout=30000)
        expect(page.get_by_text("离线规则演示", exact=True)).to_be_visible()
        expect(page.locator(".report-card")).to_be_visible(timeout=30000)
        page.locator(".report-card .text-button").first.click()
        expect(page.get_by_role("heading", name="核对原文")).to_be_visible()
        page.screenshot(path=str(output / "02-evidence.png"), full_page=True)
        page.get_by_role("button", name="关闭原文", exact=True).click()
        page.locator(".tabs").get_by_role("button", name=re.compile("待确认")).click()
        page.get_by_placeholder("输入确认结果，或留空稍后处理").fill("王宁")
        page.locator(".tabs").get_by_role("button", name=re.compile("任务与变化")).click()
        expect(page.locator(".action-card").first).to_be_visible()
        page.screenshot(path=str(output / "03-review.png"), full_page=True)
        page.get_by_role("button", name="确认所选变更", exact=True).click()
        expect(page.get_by_text("本次确认已留痕", exact=True)).to_be_visible(timeout=30000)
        with page.expect_download() as download:
            page.get_by_role("link", name="导出", exact=True).click()
        export_path = output / download.value.suggested_filename
        download.value.save_as(export_path)
        exported = export_path.read_text(encoding="utf-8")
        assert "王宁" in exported and "原文依据" in exported
        page.get_by_role("button", name="查看任务", exact=True).click()
        expect(page.locator(".task-card").first).to_be_visible()
        page.locator(".task-card select").first.select_option("in_progress")
        expect(page.get_by_text("任务状态已保存，变更已留痕。", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "04-tasks.png"), full_page=True)
        page.get_by_role("button", name="会议", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "05-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile horizontal overflow"
        assert not errors, errors
        print("Browser smoke passed: demo, SSE/result, evidence, questions, approval, export, tasks, mobile; no page errors.")
        print(f"Screenshots: {output.resolve()}")
        browser.close()


if __name__ == "__main__":
    main()
