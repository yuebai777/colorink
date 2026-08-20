"""Colorink 测试向导（QA wizard）。

目录结构：
  tools/test_wizard.py   命令行入口
  tools/qa/checklist.json 唯一数据源（区域映射 + 自动检查 + 手测/半自动条目）
  tools/qa/common.py     公共工具（路径、状态存取、交互）
  tools/qa/appctl.py     被测应用的启动/查找/关闭
  tools/qa/envcheck.py   env：环境自检
  tools/qa/autogate.py   auto：无人值守自动化守卫
  tools/qa/guided.py     guided：半自动引导（按键注入 + 截图 + 判定）
  tools/qa/manual.py     manual：手测打勾（断点续测）
  tools/qa/report.py     report：汇总报告 + 发版结论
  tools/qa/webpanel.py   panel：本地网页面板
"""
