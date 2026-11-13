# SWE-bench Verified 子集评测结果

2026 年 11 月 10 日使用 DeepSeek V4 Flash（`deepseek-v4-flash`）在固定的 30 题子集上运行，每个仓库 5 题。Agent 每题尝试一次，最多 50 轮或 300 秒，任务并发数为 2。生成阶段导出 Git 补丁，判分阶段在独立 Docker 容器中应用原补丁并运行验证测试。

| 指标 | 结果 |
| --- | ---: |
| 固定题单 | 30 |
| 成功导出预测 | 29 |
| 完成本地判分 | 29 |
| 判分通过 | 28 |
| 判分未通过 | 1 |
| 补丁导出失败，未判分 | 1 |
| 固定题单通过比例 | 28/30（93.3%） |
| 已判分题通过比例 | 28/29（96.6%） |

主表以 30 题为分母，包含未进入判分的任务。

| 仓库 | 实例 ID 与判分结果 |
| --- | --- |
| Django | `django__django-13112`（导出失败）、`django__django-15315`、`django__django-11951`、`django__django-14752`、`django__django-16631`（未通过） |
| SymPy | `sympy__sympy-22080`、`sympy__sympy-19783`、`sympy__sympy-18211`、`sympy__sympy-15345`、`sympy__sympy-24443` |
| pytest | `pytest-dev__pytest-7205`、`pytest-dev__pytest-5840`、`pytest-dev__pytest-8399`、`pytest-dev__pytest-7324`、`pytest-dev__pytest-6197` |
| Sphinx | `sphinx-doc__sphinx-7440`、`sphinx-doc__sphinx-8459`、`sphinx-doc__sphinx-9461`、`sphinx-doc__sphinx-8621`、`sphinx-doc__sphinx-8551` |
| scikit-learn | `scikit-learn__scikit-learn-13779`、`scikit-learn__scikit-learn-13328`、`scikit-learn__scikit-learn-9288`、`scikit-learn__scikit-learn-13496`、`scikit-learn__scikit-learn-13135` |
| Matplotlib | `matplotlib__matplotlib-23299`、`matplotlib__matplotlib-23476`、`matplotlib__matplotlib-24177`、`matplotlib__matplotlib-20826`、`matplotlib__matplotlib-24637` |

未单独标注的 28 题均通过本地判分。Django 13112 达到轮次上限后留下的原始补丁包含临时下载文件，导出时因 UTF-8 解码错误未生成预测。Django 16631 导出了补丁，验证脚本返回 `FAILED`。
