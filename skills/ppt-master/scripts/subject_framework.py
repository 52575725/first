#!/usr/bin/env python3
"""Subject-aware scene framework selection for PPT-to-video generation.

This module is intentionally deterministic.  It does not fetch images or call a
model; it only classifies content and returns a reusable rendering contract that
the component recommender, visual asset planner, and video renderer can share.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

try:
    from subject_backgrounds import background_pattern_for_subject, content_motifs_for_text
except Exception:  # pragma: no cover - keep subject detection usable standalone
    background_pattern_for_subject = None
    content_motifs_for_text = None


FORMULA_RE = re.compile(
    r"(\\sqrt|√|[A-Za-z]\s*[=<>+\-*/^]\s*[\w(]|"
    r"\d+\s*[+\-*/^]\s*\d+|F\s*=|E\s*=|v\s*=|a\s*=|μ|公式|方程|函数|定理|证明|推导|计算)"
)


SUBJECT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "literature": {
        "family": "humanities",
        "label": "语文/文学",
        "keywords": [
            "语文", "文学", "课文", "文本", "朗读", "阅读", "赏析", "修辞", "比喻", "拟人",
            "意象", "主题", "情感", "人物形象", "环境描写", "语言特色", "散文", "小说", "诗歌",
            "诗词", "文言文", "史铁生", "我与地坛", "地坛", "鲁迅", "朱自清", "老舍", "余华",
            "母亲", "背影", "车辙", "脚印", "生命", "写作", "细节描写",
            "文本线索", "母亲形象", "情感变化", "联读", "秋天的怀念", "合作探究", "拓展表达",
        ],
        "strong": ["我与地坛", "史铁生", "课文", "文本细读", "意象", "人物形象", "母亲形象", "文本线索"],
    },
    "history": {
        "family": "humanities",
        "label": "历史",
        "keywords": [
            "历史", "朝代", "世纪", "公元", "革命", "战争", "改革", "条约", "事件", "背景",
            "原因", "影响", "意义", "时间线", "阶段", "制度", "王朝", "近代", "古代", "辛亥",
            "五四", "抗战", "工业革命",
        ],
        "strong": ["历史", "时间线", "革命", "朝代", "战争"],
    },
    "politics": {
        "family": "humanities",
        "label": "政治/法治",
        "keywords": [
            "政治", "法治", "宪法", "民主", "权利", "义务", "制度", "国家", "政策",
            "价值观", "经济制度", "社会责任", "公民", "法律", "治理", "共同体",
        ],
        "strong": ["政治", "法治", "宪法", "公民"],
    },
    "geography": {
        "family": "humanities",
        "label": "地理",
        "keywords": [
            "地理", "地图", "气候", "季风", "河流", "地形", "地貌", "经纬度", "等高线",
            "等值线", "洋流", "人口", "城市化", "区域", "板块", "降水", "温度", "地球",
            "自然带", "农业区位",
        ],
        "strong": ["地理", "地图", "气候", "等高线", "经纬度"],
    },
    "math": {
        "family": "stem",
        "label": "数学",
        "keywords": [
            "数学", "根号", "平方根", "算术平方根", "二次根式", "函数", "方程", "不等式",
            "定义域", "值域", "数轴", "坐标", "图像", "几何", "三角形", "勾股", "面积",
            "化简", "求解", "证明", "定理", "公式", "例题", "计算", "√", "\\sqrt",
        ],
        "strong": ["根号", "平方根", "函数", "方程", "几何", "√", "\\sqrt"],
    },
    "physics": {
        "family": "stem",
        "label": "物理",
        "keywords": [
            "物理", "摩擦力", "摩擦", "压力", "正压力", "弹力", "重力", "受力", "受力分析",
            "速度", "加速度", "牛顿", "运动", "电路", "电压", "电流", "电阻", "功率",
            "压强", "浮力", "实验", "测力计", "F=", "F =", "μ", "N",
        ],
        "strong": ["物理", "摩擦力", "受力分析", "测力计", "牛顿", "F=", "μ"],
    },
    "chemistry": {
        "family": "stem",
        "label": "化学",
        "keywords": [
            "化学", "分子", "原子", "离子", "元素", "化学式", "化学方程式", "反应",
            "酸碱", "溶液", "沉淀", "氧化", "还原", "催化", "实验", "试管", "物质",
        ],
        "strong": ["化学", "化学方程式", "反应", "酸碱", "溶液"],
    },
    "biology": {
        "family": "stem",
        "label": "生物",
        "keywords": [
            "生物", "细胞", "DNA", "基因", "遗传", "光合作用", "呼吸作用", "生态",
            "酶", "器官", "组织", "神经", "免疫", "植物", "动物", "种群",
        ],
        "strong": ["生物", "细胞", "DNA", "基因", "光合作用"],
    },
    "computer": {
        "family": "stem",
        "label": "信息技术",
        "keywords": [
            "编程", "程序", "代码", "算法", "数据结构", "Python", "Java", "C++", "AI",
            "人工智能", "网络", "数据库", "二进制", "编码", "模型训练", "计算机",
        ],
        "strong": ["算法", "编程", "代码", "二进制", "计算机"],
    },
    "business": {
        "family": "business",
        "label": "商业/创业",
        "keywords": [
            "商业", "创业", "市场", "用户", "品牌", "产品", "营收", "利润", "成本",
            "增长", "竞争", "团队", "融资", "商业模式", "KPI", "ROI", "消费者", "政策",
        ],
        "strong": ["商业模式", "市场", "营收", "创业", "KPI", "ROI"],
    },
}


THEMES: dict[str, dict[str, str]] = {
    "literary_editorial": {
        "background": "#f7f4ee",
        "accent": "#9f5f2c",
        "accent_2": "#0f766e",
        "soft": "#fde68a@0.24",
        "soft_2": "#bae6fd@0.22",
        "text": "#1f2937",
    },
    "historical_archive": {
        "background": "#f6f1e8",
        "accent": "#7c2d12",
        "accent_2": "#1d4ed8",
        "soft": "#fed7aa@0.25",
        "soft_2": "#bfdbfe@0.20",
        "text": "#1c1917",
    },
    "map_atlas": {
        "background": "#edf7f2",
        "accent": "#047857",
        "accent_2": "#2563eb",
        "soft": "#bbf7d0@0.24",
        "soft_2": "#bfdbfe@0.22",
        "text": "#0f172a",
    },
    "notebook_formula": {
        "background": "#eef7ff",
        "accent": "#2563eb",
        "accent_2": "#16a34a",
        "soft": "#bfdbfe@0.28",
        "soft_2": "#bbf7d0@0.20",
        "text": "#0f172a",
    },
    "lab_clean": {
        "background": "#f0fdfa",
        "accent": "#0f766e",
        "accent_2": "#7c3aed",
        "soft": "#99f6e4@0.26",
        "soft_2": "#ddd6fe@0.20",
        "text": "#0f172a",
    },
    "science_grid": {
        "background": "#f5f7fb",
        "accent": "#334155",
        "accent_2": "#2563eb",
        "soft": "#dbeafe@0.26",
        "soft_2": "#e9d5ff@0.18",
        "text": "#111827",
    },
    "business_dashboard": {
        "background": "#f7f8fa",
        "accent": "#2375ff",
        "accent_2": "#10b981",
        "soft": "#dbeafe@0.25",
        "soft_2": "#dcfce7@0.20",
        "text": "#101828",
    },
    "general_clean": {
        "background": "#f7f8fa",
        "accent": "#2563eb",
        "accent_2": "#16a34a",
        "soft": "#dbeafe@0.24",
        "soft_2": "#dcfce7@0.18",
        "text": "#101828",
    },
}


LABELS: dict[str, dict[str, str]] = {
    "humanities": {
        "opener": "背景进入",
        "path": "学习路径：背景脉络 / 细节证据 / 观点归纳 / 表达迁移。",
        "point_a": "文本线索",
        "point_b": "细节赏析",
        "point_c": "主题归纳",
        "image": "情境图",
    },
    "stem": {
        "opener": "模型进入",
        "path": "学习路径：概念条件 / 公式模型 / 例题推演 / 结果检验。",
        "point_a": "条件",
        "point_b": "模型",
        "point_c": "检验",
        "image": "示意图",
    },
    "business": {
        "opener": "问题进入",
        "path": "学习路径：问题场景 / 数据证据 / 方案机制 / 落地结果。",
        "point_a": "问题",
        "point_b": "证据",
        "point_c": "方案",
        "image": "案例图",
    },
    "general": {
        "opener": "重点进入",
        "path": "学习路径：核心对象 / 关键关系 / 示例说明 / 总结迁移。",
        "point_a": "核心",
        "point_b": "关系",
        "point_c": "迁移",
        "image": "辅助图",
    },
}


SUBJECT_LABELS: dict[str, dict[str, str]] = {
    "literature": {
        "path": "学习路径：文本线索 / 细节赏析 / 情感理解 / 表达迁移。",
        "point_a": "文本线索",
        "point_b": "细节赏析",
        "point_c": "主题归纳",
        "image": "情境图",
    },
    "history": {
        "path": "学习路径：时代背景 / 事件过程 / 转折原因 / 历史影响。",
        "point_a": "史料线索",
        "point_b": "背景原因",
        "point_c": "影响归纳",
        "image": "史料图",
    },
    "politics": {
        "path": "学习路径：概念边界 / 材料依据 / 逻辑关系 / 观点表达。",
        "point_a": "概念边界",
        "point_b": "材料依据",
        "point_c": "观点表达",
        "image": "材料图",
    },
    "geography": {
        "path": "学习路径：空间位置 / 分布特征 / 成因机制 / 图像判读。",
        "point_a": "空间位置",
        "point_b": "成因机制",
        "point_c": "图像判读",
        "image": "地图图解",
    },
    "math": {
        "point_a": "条件",
        "point_b": "公式",
        "point_c": "检验",
        "image": "公式图",
    },
    "physics": {
        "path": "学习路径：现象观察 / 受力建模 / 公式关系 / 应用判断。",
        "point_a": "现象条件",
        "point_b": "模型公式",
        "point_c": "应用判断",
        "image": "实验图",
    },
    "chemistry": {
        "path": "学习路径：实验现象 / 微观解释 / 反应关系 / 结论判断。",
        "point_a": "实验现象",
        "point_b": "微观解释",
        "point_c": "反应判断",
        "image": "实验图",
    },
    "biology": {
        "path": "学习路径：结构特征 / 过程机制 / 功能联系 / 生命意义。",
        "point_a": "结构特征",
        "point_b": "过程机制",
        "point_c": "功能联系",
        "image": "结构图",
    },
    "computer": {
        "path": "学习路径：输入结构 / 算法流程 / 运行结果 / 复杂度判断。",
        "point_a": "输入结构",
        "point_b": "算法流程",
        "point_c": "结果验证",
        "image": "流程图",
    },
}


COMPONENT_POOLS: dict[str, list[str]] = {
    "literature": [
        "magazine_spread", "quote_focus", "split_text_visual", "photo_story",
        "image_mosaic", "application_storyboard", "rounded_step_cards", "insight_cards",
    ],
    "history": [
        "timeline", "roadmap_timeline", "map_focus", "photo_story", "before_after",
        "two_column_compare", "insight_cards", "dense_grid",
    ],
    "politics": [
        "two_column_compare", "capability_matrix", "problem_stack", "solution_flow",
        "insight_cards", "statement_focus", "rounded_step_cards",
    ],
    "geography": [
        "map_focus", "split_text_visual", "chart_focus", "process_flow",
        "two_column_compare", "image_hero", "photo_story",
    ],
    "math": [
        "blackboard_derivation", "formula_walkthrough", "radial_concept_map",
        "checkpoint_ladder", "misconception_compare", "rounded_step_cards",
        "problem_stack", "chart_focus",
    ],
    "physics": [
        "formula_walkthrough", "blackboard_derivation", "process_flow",
        "split_text_visual", "application_storyboard", "checkpoint_ladder",
        "misconception_compare",
    ],
    "chemistry": [
        "process_flow", "split_text_visual", "application_storyboard",
        "checkpoint_ladder", "misconception_compare", "lifecycle_loop",
    ],
    "biology": [
        "lifecycle_loop", "split_text_visual", "process_flow", "image_mosaic",
        "radial_concept_map", "checkpoint_ladder",
    ],
    "computer": [
        "process_flow", "dense_grid", "radial_concept_map", "solution_flow",
        "two_column_compare", "chart_focus",
    ],
    "business": [
        "market_dashboard", "revenue_model", "metric_dashboard", "problem_stack",
        "solution_flow", "lifecycle_loop", "flywheel", "team_roster",
    ],
    "general": [
        "rounded_step_cards", "insight_cards", "solution_flow", "split_text_visual",
        "statement_focus", "section_title",
    ],
}


SCENE_COMPONENT_OVERRIDES: dict[str, list[str]] = {
    "cover": ["cover_hero", "magazine_spread", "section_title", "image_hero"],
    "objective": ["rounded_step_cards", "checkpoint_ladder", "insight_cards"],
    "reading_path": ["process_flow", "timeline", "magazine_spread"],
    "quote_analysis": ["quote_focus", "magazine_spread", "split_text_visual"],
    "close_reading": ["magazine_spread", "quote_focus", "rounded_step_cards"],
    "character_analysis": ["photo_story", "split_text_visual", "insight_cards"],
    "emotion_curve": ["timeline", "roadmap_timeline", "quote_focus"],
    "comparison_reading": ["two_column_compare", "before_after", "magazine_spread"],
    "discussion": ["insight_cards", "rounded_step_cards", "solution_flow"],
    "writing_task": ["application_storyboard", "checkpoint_ladder", "rounded_step_cards"],
    "timeline": ["timeline", "roadmap_timeline", "map_focus"],
    "compare": ["two_column_compare", "misconception_compare", "before_after"],
    "definition": ["radial_concept_map", "rounded_step_cards", "blackboard_derivation"],
    "example": ["problem_stack", "formula_walkthrough", "checkpoint_ladder"],
    "exercise": ["formula_walkthrough", "problem_stack", "checkpoint_ladder"],
    "formula": ["blackboard_derivation", "formula_walkthrough", "radial_concept_map"],
    "derivation": ["blackboard_derivation", "formula_walkthrough"],
    "experiment": ["process_flow", "split_text_visual", "application_storyboard"],
    "variable_control": ["two_column_compare", "process_flow", "misconception_compare"],
    "application": ["application_storyboard", "split_text_visual", "photo_story"],
    "misconception": ["misconception_compare", "checkpoint_ladder", "problem_stack"],
    "data": ["chart_focus", "metric_dashboard", "kpi_cards"],
    "case": ["application_storyboard", "split_text_visual", "photo_story"],
    "process": ["process_flow", "solution_flow", "lifecycle_loop"],
    "summary": ["checkpoint_ladder", "rounded_step_cards", "insight_cards"],
    "concept": ["radial_concept_map", "rounded_step_cards", "insight_cards"],
}


QUERY_STOPWORDS = {
    "本页", "这一页", "页面", "内容", "学习", "理解", "掌握", "说明", "分析", "重点",
    "问题", "方法", "条件", "应用", "探究", "例题", "要求", "目标", "活动", "任务",
}

QUERY_TRANSLATIONS = {
    "我与地坛": "Temple of Earth Beijing Shi Tiesheng",
    "史铁生": "Shi Tiesheng",
    "地坛": "Temple of Earth Beijing",
    "母亲": "mother character literary essay",
    "母亲形象": "mother character literary essay",
    "情感": "literary emotion",
    "情感变化": "emotional change literature",
    "文本": "text close reading",
    "文本线索": "textual clues literature",
    "意象": "literary imagery",
    "散文": "Chinese prose essay",
    "修辞": "rhetoric literature",
    "作者": "author portrait",
    "历史": "history",
    "时间线": "history timeline",
    "地图": "map",
    "地理": "geography",
    "气候": "climate map",
    "等高线": "contour map",
    "经纬度": "latitude longitude map",
    "根号": "square root",
    "平方根": "square root",
    "定义域": "domain number line",
    "函数": "function graph",
    "方程": "equation worked example",
    "不等式": "inequality number line",
    "摩擦力": "friction force",
    "摩擦": "friction force",
    "受力分析": "force diagram",
    "正压力": "normal force",
    "测力计": "spring scale experiment",
    "化学实验": "chemistry experiment",
    "细胞": "cell biology diagram",
    "算法": "algorithm diagram",
    "商业": "business case",
    "市场": "market data",
}


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _join_text(title: Any = "", rest: Iterable[Any] | None = None) -> str:
    parts = [normalize_text(title)]
    if rest:
        parts.extend(normalize_text(item) for item in rest)
    return " ".join(part for part in parts if part)


def _keyword_score(text: str, keywords: list[str], strong: list[str]) -> float:
    score = 0.0
    lower = text.lower()
    for keyword in keywords:
        if keyword and keyword.lower() in lower:
            score += 1.0 + min(1.2, len(keyword) / 8)
    for keyword in strong:
        if keyword and keyword.lower() in lower:
            score += 2.4
    return score


def analyze_subject(text: str, title: str = "", slide_num: int = 0) -> dict[str, Any]:
    """Return the dominant subject and family for a slide or project text."""
    combined = _join_text(title, [text])
    if not combined:
        return {
            "subject": "general",
            "family": "general",
            "label": "通用",
            "confidence": 0.0,
            "scores": {},
        }

    scores: dict[str, float] = {}
    for subject, profile in SUBJECT_DEFINITIONS.items():
        scores[subject] = _keyword_score(combined, profile["keywords"], profile["strong"])

    if FORMULA_RE.search(combined):
        scores["math"] += 2.0
    if any(token in combined for token in ("F=", "F =", "μ", "牛顿", "测力计", "受力")):
        scores["physics"] += 2.2
    if re.search(r"(19|20)\d{2}年|公元|世纪|朝代", combined):
        scores["history"] += 1.8
    if "《" in combined and "》" in combined:
        scores["literature"] += 2.2
    if "文本" in combined and "地图" in combined:
        scores["literature"] += 3.0
        scores["geography"] = max(0.0, scores["geography"] - 2.0)
    if any(token in combined for token in ("母亲形象", "情感变化", "联读", "秋天的怀念")):
        scores["literature"] += 2.6

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    subject, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top < 2.0:
        subject = "general"
        family = "general"
        confidence = 0.25 if top else 0.0
    else:
        family = SUBJECT_DEFINITIONS[subject]["family"]
        confidence = min(0.98, 0.52 + (top - second) / max(5.0, top + 1.0))

    label = SUBJECT_DEFINITIONS.get(subject, {}).get("label", "通用")
    return {
        "subject": subject,
        "family": family,
        "label": label,
        "confidence": round(confidence, 2),
        "scores": {key: round(value, 2) for key, value in ranked if value > 0},
    }


def detect_scene(text: str, subject: str = "general", family: str = "general", slide_num: int = 0) -> str:
    combined = normalize_text(text)
    compact = re.sub(r"\s+", "", combined)
    if slide_num == 1 or re.search(r"(封面|导入|课题|主题|目录|contents?)", combined, re.I) and len(compact) < 80:
        return "cover"
    if any(marker in combined for marker in ("总结", "小结", "回顾", "课堂小结", "本课小结", "作业", "谢谢")):
        return "summary"
    if any(marker in combined for marker in ("学习目标", "教学目标", "学习任务", "本课目标", "目标")) and len(compact) < 220:
        return "objective"
    if subject == "literature":
        if any(marker in combined for marker in ("导入问题", "为什么", "追问", "问题导入")):
            return "discussion"
        if any(marker in combined for marker in ("文本线索", "阅读路径", "文章脉络", "行文思路", "结构梳理")):
            return "reading_path"
        if any(marker in combined for marker in ("情感变化", "情绪变化", "心理变化", "情感脉络")):
            return "emotion_curve"
        if any(marker in combined for marker in ("联读", "比较阅读", "对比阅读", "群文", "迁移阅读")):
            return "comparison_reading"
        if any(marker in combined for marker in ("合作探究", "讨论", "探究问题", "探究", "小组", "交流")):
            return "discussion"
        if any(marker in combined for marker in ("写作", "表达", "仿写", "拓展表达", "作业")):
            return "writing_task"
    if family == "stem":
        if any(marker in combined for marker in ("易错", "误区", "错解", "注意", "判断正误")):
            return "misconception"
        if any(marker in combined for marker in ("定义", "概念", "是什么", "条件", "产生条件")):
            return "definition"
        if any(marker in combined for marker in ("例题", "示例", "练一练", "应用题")):
            return "example"
        if any(marker in combined for marker in ("控制变量", "变量", "影响因素")):
            return "variable_control"
        if any(marker in combined for marker in ("生活应用", "实际应用", "应用场景", "案例")):
            return "application"
    if re.search(r"(总结|小结|回顾|复习|课堂练习|巩固|检测|作业|谢谢|感谢)", combined):
        return "summary"
    if re.search(r"(例题|练习|求解|计算|化简|证明|已知|求|判断正误)", combined):
        return "exercise"
    if re.search(r"(推导|证明|由此可得|因此|所以|化简步骤)", combined):
        return "derivation"
    if FORMULA_RE.search(combined) or re.search(r"(公式|法则|性质|定义域|函数图像)", combined):
        return "formula"
    if re.search(r"(实验|观察|探究|器材|步骤|变量|现象|结论)", combined):
        return "experiment"
    if re.search(r"(时间线|阶段|年代|世纪|公元|革命|战争|发展历程|\d{3,4}年)", combined):
        return "timeline"
    if re.search(r"(对比|比较|相同|不同|优缺点|正误|误区|区别|VS)", combined, re.I):
        return "compare"
    if re.search(r"([“”\"].{4,}[“”\"]|原文|摘录|引用|名句)", combined):
        return "quote_analysis"
    if subject == "literature":
        if re.search(r"(人物|形象|心理|性格|母亲|作者)", combined):
            return "character_analysis"
        if re.search(r"(意象|细节|赏析|修辞|表达效果|情感|主题)", combined):
            return "close_reading"
        return "concept"
    if subject == "geography" and re.search(r"(地图|区域|分布|经纬|等高|等值|气候)", combined):
        return "case"
    if re.search(r"(数据|比例|增长|趋势|指标|KPI|ROI|表格|图表|统计)", combined, re.I):
        return "data"
    if re.search(r"(流程|步骤|路径|机制|从.+到|闭环|循环)", combined):
        return "process"
    if re.search(r"(案例|场景|生活|应用|情境|示例)", combined):
        return "case"
    return "concept"


def background_style_for(subject: str, family: str) -> str:
    if subject == "literature":
        return "literary_editorial"
    if subject == "history":
        return "historical_archive"
    if subject == "geography":
        return "map_atlas"
    if subject == "math":
        return "notebook_formula"
    if subject in {"physics", "chemistry", "biology"}:
        return "lab_clean"
    if subject == "computer":
        return "science_grid"
    if family == "business":
        return "business_dashboard"
    return "general_clean"


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def component_pool_for(subject: str, scene: str) -> list[str]:
    base = COMPONENT_POOLS.get(subject) or COMPONENT_POOLS["general"]
    scene_first = SCENE_COMPONENT_OVERRIDES.get(scene, [])
    return _unique([*scene_first, *base])[:8]


def labels_for(subject: str, family: str) -> dict[str, str]:
    labels = dict(LABELS.get(family, LABELS["general"]))
    labels.update(SUBJECT_LABELS.get(subject, {}))
    return labels


def image_policy_for(subject: str, family: str, scene: str, text: str) -> tuple[str, str]:
    """Return (image_policy, asset_mode)."""
    if scene in {
        "cover",
        "objective",
        "reading_path",
        "quote_analysis",
        "close_reading",
        "character_analysis",
        "emotion_curve",
        "comparison_reading",
        "discussion",
        "writing_task",
    } and family == "humanities":
        return "prefer", "search"
    if subject in {"history", "geography"} and scene in {"timeline", "case", "compare"}:
        return "prefer", "search"
    if family == "humanities":
        return ("prefer", "generate") if len(text) > 26 else ("optional", "generate")
    if subject == "math" and scene in {"definition", "formula", "derivation", "exercise", "example", "misconception"}:
        return "prefer", "formula_render"
    if subject in {"physics", "chemistry", "biology"} and scene in {
        "definition",
        "formula",
        "experiment",
        "variable_control",
        "case",
        "process",
        "application",
        "misconception",
    }:
        return "prefer", "diagram"
    if subject == "computer" and scene in {"process", "concept", "data"}:
        return "optional", "diagram"
    if family == "business" and scene in {"case", "data", "process"}:
        return "optional", "search"
    if scene in {"data", "summary"}:
        return "none", "none"
    return "optional", "generate"


def page_type_for_scene(scene: str, family: str) -> str:
    if scene in {"cover", "summary"}:
        return scene
    if scene in {"objective", "definition", "concept"}:
        return "concept_intro"
    if scene in {"reading_path", "process", "timeline", "emotion_curve"}:
        return "sequence"
    if scene in {"quote_analysis", "close_reading", "character_analysis"}:
        return "evidence_analysis"
    if scene in {"comparison_reading", "compare", "variable_control", "misconception"}:
        return "comparison"
    if scene in {"exercise", "example", "derivation", "formula"}:
        return "worked_example"
    if scene in {"experiment", "application", "case"}:
        return "scenario"
    if scene in {"discussion", "writing_task"}:
        return "activity"
    if scene == "data":
        return "data"
    return "humanities_concept" if family == "humanities" else "concept"


def scene_tags_for(subject: str, family: str, scene: str) -> list[str]:
    tags = [family, subject, scene, page_type_for_scene(scene, family)]
    if family == "humanities":
        tags.append("image_rich")
        if scene in {"quote_analysis", "close_reading", "character_analysis"}:
            tags.append("text_evidence")
    if family == "stem":
        tags.append("diagram_first")
        if scene in {"exercise", "example", "formula", "derivation"}:
            tags.append("step_by_step")
    return _unique([tag for tag in tags if tag and tag != "general"])


def _extract_query_terms(text: str, subject: str, limit: int = 5) -> list[str]:
    terms: list[str] = []
    for match in re.findall(r"《([^》]{2,24})》", text):
        terms.append(match)
    for keyword in SUBJECT_DEFINITIONS.get(subject, {}).get("keywords", []):
        if keyword in text and keyword not in QUERY_STOPWORDS:
            terms.append(keyword)
    for token in re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9+\-]{2,}", text):
        if token not in QUERY_STOPWORDS and len(token) <= 12:
            terms.append(token)
    return _unique(terms)[:limit]


def query_hints_for(subject: str, family: str, scene: str, text: str) -> list[str]:
    terms = _extract_query_terms(text, subject, limit=4)
    topic = " ".join(terms) if terms else SUBJECT_DEFINITIONS.get(subject, {}).get("label", "presentation")
    translated_terms = _unique(
        QUERY_TRANSLATIONS.get(term, term)
        for term in terms
        if QUERY_TRANSLATIONS.get(term) or re.search(r"[A-Za-z]", term)
    )
    english_topic = " ".join(translated_terms[:5]) or topic
    if subject == "literature":
        return _unique([
            f"{english_topic} literature contextual photo",
            f"{english_topic} literary illustration",
            f"{topic} 文学 意象 插图",
        ])
    if subject == "history":
        return _unique([
            f"{english_topic} historical map timeline",
            f"{english_topic} historical event photo",
            f"{topic} 历史 时间线 地图",
        ])
    if subject == "geography":
        return _unique([
            f"{english_topic} geographic diagram map",
            f"{english_topic} atlas map",
            f"{topic} 地图 示意图",
        ])
    if subject == "math":
        return _unique([
            f"{english_topic} formula diagram worked example",
            f"{english_topic} function graph number line",
            f"{topic} 数学公式 示意图 例题",
        ])
    if subject == "physics":
        return _unique([
            f"{english_topic} physics force diagram experiment",
            f"{english_topic} classroom experiment diagram",
            f"{topic} 物理 受力分析 示意图",
        ])
    if subject == "chemistry":
        return _unique([
            f"{english_topic} chemistry reaction diagram",
            f"{english_topic} chemistry experiment photo",
            f"{topic} 化学实验 示意图",
        ])
    if subject == "biology":
        return _unique([
            f"{english_topic} biology process diagram",
            f"{english_topic} biology structure illustration",
            f"{topic} 生物结构 示意图",
        ])
    if subject == "computer":
        return _unique([
            f"{english_topic} algorithm diagram",
            f"{english_topic} data structure visualization",
            f"{topic} 信息技术 流程图",
        ])
    if subject == "business":
        return _unique([
            f"{english_topic} business case photo",
            f"{english_topic} market data dashboard",
            f"{topic} product scenario photo",
        ])
    return [f"{topic} presentation visual"]


def framework_for_slide(
    title: str = "",
    rest: Iterable[Any] | None = None,
    slide_num: int = 0,
    layout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _join_text(title, rest)
    subject_info = analyze_subject(text, title=title, slide_num=slide_num)
    subject = subject_info["subject"]
    family = subject_info["family"]
    scene = detect_scene(text, subject=subject, family=family, slide_num=slide_num)
    image_policy, asset_mode = image_policy_for(subject, family, scene, text)
    background_style = background_style_for(subject, family)
    labels = labels_for(subject, family)
    background_pattern = (
        background_pattern_for_subject(subject, family, scene)
        if background_pattern_for_subject is not None
        else {"id": background_style, "subject": subject, "scene": scene}
    )
    content_motifs = (
        content_motifs_for_text(subject, scene, text)
        if content_motifs_for_text is not None
        else []
    )
    return {
        "subject": subject,
        "subject_label": subject_info["label"],
        "family": family,
        "scene": scene,
        "page_type": page_type_for_scene(scene, family),
        "scene_tags": scene_tags_for(subject, family, scene),
        "confidence": subject_info["confidence"],
        "image_policy": image_policy,
        "asset_mode": asset_mode,
        "background_style": background_style,
        "background_pattern": background_pattern,
        "content_motifs": content_motifs,
        "theme": THEMES[background_style],
        "component_pool": component_pool_for(subject, scene),
        "query_hints": query_hints_for(subject, family, scene, text),
        "labels": labels,
        "scores": subject_info.get("scores", {}),
    }


def project_subject_profile(slides: Iterable[dict[str, Any]] | str | Path) -> dict[str, Any]:
    """Classify a whole project or slide list by aggregating slide text."""
    if isinstance(slides, (str, Path)):
        project = Path(slides)
        structure = project / "slide_structure.json"
        if not structure.exists():
            return framework_for_slide(project.name, [], 0)
        data = json.loads(structure.read_text(encoding="utf-8"))
        slide_items = data.get("slides", data) if isinstance(data, dict) else data
    else:
        slide_items = list(slides)

    parts: list[str] = []
    for slide in slide_items:
        parts.append(str(slide.get("title", "")))
        parts.extend(str(item) for item in slide.get("bullets", [])[:8])
        parts.extend(str(item) for item in slide.get("paragraphs", [])[:5])
        for block in slide.get("text_blocks", [])[:10]:
            parts.append(str(block.get("text", "")))
    return framework_for_slide(" ".join(parts[:80]), [], 0)


def framework_component_score(framework: dict[str, Any], component_id: str) -> float:
    """Return a score boost for components in the selected framework pool."""
    pool = framework.get("component_pool") or []
    if component_id not in pool:
        return 0.0
    index = pool.index(component_id)
    return max(0.62, 0.94 - index * 0.045)


def is_humanities_framework(framework: dict[str, Any]) -> bool:
    return framework.get("family") == "humanities"


def is_stem_framework(framework: dict[str, Any]) -> bool:
    return framework.get("family") == "stem"
