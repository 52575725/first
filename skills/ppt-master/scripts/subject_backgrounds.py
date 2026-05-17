#!/usr/bin/env python3
"""Deterministic subject background pattern specs.

The renderer consumes the returned operations and turns them into ffmpeg
layers.  This file deliberately avoids assets and network access so every new
PPT/doc can reuse the same subject-aware visual language before any page-level
layout runs.
"""

from __future__ import annotations

from typing import Any


PatternOp = dict[str, Any]


PATTERN_BY_SUBJECT: dict[str, dict[str, Any]] = {
    "literature": {
        "id": "literary_paper_branch",
        "family": "humanities",
        "density": "rich",
        "keywords": ["paper", "corner", "branch", "blossom"],
    },
    "history": {
        "id": "archive_timeline",
        "family": "humanities",
        "density": "medium",
        "keywords": ["archive", "stamp", "timeline"],
    },
    "politics": {
        "id": "civic_document",
        "family": "humanities",
        "density": "medium",
        "keywords": ["document", "seal", "policy"],
    },
    "geography": {
        "id": "atlas_contour",
        "family": "humanities",
        "density": "medium",
        "keywords": ["atlas", "contour", "grid"],
    },
    "math": {
        "id": "math_graph_paper",
        "family": "stem",
        "density": "medium",
        "keywords": ["grid", "axis", "graph"],
    },
    "physics": {
        "id": "physics_vector_lab",
        "family": "stem",
        "density": "medium",
        "keywords": ["vector", "force", "diagram"],
    },
    "chemistry": {
        "id": "chemistry_molecule_lab",
        "family": "stem",
        "density": "medium",
        "keywords": ["molecule", "bond", "lab"],
    },
    "biology": {
        "id": "biology_cell_lab",
        "family": "stem",
        "density": "medium",
        "keywords": ["cell", "dna", "process"],
    },
    "computer": {
        "id": "computer_circuit_grid",
        "family": "stem",
        "density": "medium",
        "keywords": ["circuit", "node", "data"],
    },
    "business": {
        "id": "business_dashboard_grid",
        "family": "business",
        "density": "medium",
        "keywords": ["dashboard", "metric", "trend"],
    },
    "general": {
        "id": "general_soft_bands",
        "family": "general",
        "density": "light",
        "keywords": ["neutral", "bands"],
    },
}


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    return text if text.startswith("#") and len(text) in {4, 7} else fallback


def _alpha(color: str, amount: float) -> str:
    return f"{color}@{max(0.0, min(1.0, amount)):.3f}"


def _rect(x: int, y: int, w: int, h: int, color: str, start: float = 0.0) -> PatternOp:
    return {"kind": "rect", "box": [int(x), int(y), int(w), int(h)], "color": color, "start": float(start)}


def _roundrect(
    x: int,
    y: int,
    w: int,
    h: int,
    color: str,
    radius: int = 16,
    start: float = 0.0,
) -> PatternOp:
    return {
        "kind": "roundrect",
        "box": [int(x), int(y), int(w), int(h)],
        "color": color,
        "radius": int(radius),
        "start": float(start),
    }


def _circle(cx: int, cy: int, radius: int, color: str, start: float = 0.0) -> PatternOp:
    return {
        "kind": "circle",
        "cx": int(cx),
        "cy": int(cy),
        "radius": int(radius),
        "color": color,
        "start": float(start),
    }


def _line(x1: int, y1: int, x2: int, y2: int, color: str, thickness: int = 3, start: float = 0.0) -> PatternOp:
    return {
        "kind": "line",
        "x1": int(x1),
        "y1": int(y1),
        "x2": int(x2),
        "y2": int(y2),
        "color": color,
        "thickness": int(thickness),
        "start": float(start),
    }


def _diag(x1: int, y1: int, x2: int, y2: int, color: str, thickness: int = 4, pieces: int = 24) -> PatternOp:
    return {
        "kind": "diag",
        "x1": int(x1),
        "y1": int(y1),
        "x2": int(x2),
        "y2": int(y2),
        "color": color,
        "thickness": int(thickness),
        "pieces": int(pieces),
        "start": 0.0,
    }


def _text(text: str, x: int, y: int, size: int, color: str, start: float = 0.0) -> PatternOp:
    return {
        "kind": "text",
        "text": str(text),
        "x": int(x),
        "y": int(y),
        "font_size": int(size),
        "color": color,
        "bold": True,
        "start": float(start),
    }


def _corner_ops(x: int, y: int, sx: int, sy: int, color: str, accent: str) -> list[PatternOp]:
    """Return square-corner ornament lines, scaled by sx/sy signs."""
    return [
        _line(x, y, x + sx * 120, y, color, 4),
        _line(x, y, x, y + sy * 120, color, 4),
        _line(x + sx * 28, y + sy * 28, x + sx * 112, y + sy * 28, color, 3),
        _line(x + sx * 28, y + sy * 28, x + sx * 28, y + sy * 112, color, 3),
        _line(x + sx * 58, y + sy * 58, x + sx * 106, y + sy * 58, accent, 2),
        _line(x + sx * 58, y + sy * 58, x + sx * 58, y + sy * 106, accent, 2),
    ]


def _literary_branch_ops(accent: str, accent_2: str) -> list[PatternOp]:
    blush = _alpha("#d9487a", 0.150)
    light = _alpha("#f9a8d4", 0.180)
    ink = _alpha(accent_2, 0.230)
    ops = [
        _diag(1605, 92, 1810, 292, ink, 5, 30),
        _diag(1695, 118, 1830, 108, _alpha(accent, 0.135), 3, 18),
        _diag(1688, 174, 1818, 238, _alpha(accent, 0.150), 3, 18),
        _diag(125, 900, 310, 825, ink, 5, 28),
        _diag(182, 875, 235, 775, _alpha(accent, 0.125), 3, 16),
    ]
    for idx, (cx, cy, r) in enumerate(
        [
            (1628, 114, 7),
            (1668, 138, 10),
            (1714, 110, 8),
            (1748, 166, 12),
            (1800, 226, 9),
            (1832, 280, 7),
            (145, 905, 9),
            (198, 876, 8),
            (248, 842, 10),
            (284, 804, 7),
        ]
    ):
        ops.append(_roundrect(cx - r, cy - r, r * 2, r * 2, blush if idx % 2 else light, max(4, r)))
    return ops


def _paper_texture_ops(width: int, height: int, line_color: str) -> list[PatternOp]:
    ops: list[PatternOp] = []
    for idx, y in enumerate((106, 214, 322, 430, 538, 646, 754, 862)):
        ops.append(_rect(90, y, width - 180, 2, _alpha(line_color, 0.045 if idx % 2 else 0.035)))
    for x in (120, width - 130):
        ops.append(_rect(x, 92, 2, height - 210, _alpha(line_color, 0.050)))
    return ops


def _grid_ops(width: int, height: int, color: str, step_x: int, step_y: int, alpha: float = 0.055) -> list[PatternOp]:
    ops: list[PatternOp] = []
    for x in range(step_x, width, step_x):
        if 710 <= x <= 1210:
            continue
        ops.append(_rect(x, 0, 1, height, _alpha(color, alpha)))
    for y in range(step_y, height - 110, step_y):
        ops.append(_rect(0, y, width, 1, _alpha(color, alpha * 0.82)))
    return ops


MOTIF_RULES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "literature": [
        ("temple_gate", ("地坛", "园子", "古园", "坛", "庙", "宫门", "史铁生")),
        ("mother_shadow", ("母亲", "背影", "守望", "牵挂", "等待")),
        ("autumn_path", ("秋天", "秋风", "落叶", "树", "脚印", "园路")),
        ("wheel_track", ("车辙", "轮椅", "车轮", "脚印")),
        ("sea_waves", ("观沧海", "沧海", "海", "山岛", "洪波", "曹操")),
        ("quote_scroll", ("原文", "文本", "细节", "赏析", "引用", "句子", "意象")),
        ("writing_brush", ("写作", "表达", "仿写", "迁移")),
    ],
    "history": [
        ("archive_timeline", ("时间", "年代", "世纪", "公元", "过程", "发展")),
        ("map_route", ("战争", "迁移", "路线", "疆域", "版图", "地图")),
        ("seal_document", ("条约", "制度", "改革", "诏令", "史料")),
    ],
    "politics": [
        ("seal_document", ("宪法", "法律", "制度", "政策", "公民", "权利", "义务")),
        ("civic_balance", ("公平", "正义", "权利", "责任", "治理")),
    ],
    "geography": [
        ("contour_map", ("地形", "等高线", "等值线", "山地", "坡度")),
        ("climate_chart", ("气候", "降水", "温度", "季风", "气温")),
        ("map_pin", ("地图", "区域", "位置", "经纬", "分布")),
    ],
    "math": [
        ("radical_mark", ("根号", "平方根", "算术平方根", "√", "sqrt")),
        ("coordinate_curve", ("函数", "图像", "坐标", "数轴", "定义域", "值域")),
        ("geometry_triangle", ("几何", "三角形", "勾股", "面积", "角")),
        ("number_line", ("不等式", "区间", "数轴", "取值")),
    ],
    "physics": [
        ("friction_surface", ("摩擦", "摩擦力", "滑动", "静摩擦", "粗糙", "接触面")),
        ("force_vectors", ("受力", "压力", "重力", "支持力", "弹力", "F=", "F =")),
        ("dynamometer", ("测力计", "实验", "拉力", "弹簧")),
    ],
    "chemistry": [
        ("molecule_reaction", ("分子", "原子", "反应", "化学式", "方程式")),
        ("test_tube", ("实验", "溶液", "沉淀", "试管", "酸", "碱")),
    ],
    "biology": [
        ("cell_structure", ("细胞", "细胞膜", "细胞核", "组织")),
        ("dna_chain", ("DNA", "基因", "遗传")),
        ("leaf_process", ("光合", "呼吸", "植物", "生态")),
    ],
    "computer": [
        ("algorithm_flow", ("算法", "流程", "程序", "代码", "逻辑")),
        ("data_nodes", ("数据", "网络", "结构", "模型", "AI")),
    ],
    "business": [
        ("trend_chart", ("市场", "增长", "营收", "利润", "成本", "KPI", "ROI")),
        ("product_card", ("用户", "产品", "品牌", "场景", "案例")),
    ],
}


SCENE_FALLBACK_MOTIFS: dict[str, tuple[str, ...]] = {
    "cover": ("temple_gate",),
    "quote_analysis": ("quote_scroll",),
    "close_reading": ("quote_scroll",),
    "character_analysis": ("mother_shadow",),
    "emotion_curve": ("autumn_path",),
    "reading_path": ("autumn_path",),
    "writing_task": ("writing_brush",),
    "experiment": ("dynamometer",),
    "formula": ("coordinate_curve",),
    "example": ("number_line",),
}


SUBJECT_FALLBACK_MOTIFS: dict[str, tuple[str, ...]] = {
    "literature": ("quote_scroll", "autumn_path"),
    "history": ("archive_timeline",),
    "politics": ("seal_document",),
    "geography": ("map_pin",),
    "math": ("coordinate_curve",),
    "physics": ("force_vectors",),
    "chemistry": ("molecule_reaction",),
    "biology": ("cell_structure",),
    "computer": ("algorithm_flow",),
    "business": ("trend_chart",),
}


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def content_motifs_for_text(subject: str, scene: str, text: str, limit: int = 3) -> list[str]:
    """Pick content-specific background motifs from slide text."""
    subject = subject or "general"
    scene = scene or "concept"
    text = str(text or "")
    lower = text.lower()
    motifs: list[str] = []
    for motif, keywords in MOTIF_RULES.get(subject, []):
        if any(keyword and keyword.lower() in lower for keyword in keywords):
            motifs.append(motif)
    if not motifs:
        motifs.extend(SCENE_FALLBACK_MOTIFS.get(scene, ()))
    if not motifs:
        motifs.extend(SUBJECT_FALLBACK_MOTIFS.get(subject, ()))
    return _unique_strings(motifs)[: max(1, int(limit or 3))]


def _arrow_ops(x1: int, y1: int, x2: int, y2: int, color: str, thickness: int = 5) -> list[PatternOp]:
    head = max(12, thickness * 4)
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 >= x1 else -1
        return [
            _line(x1, y1, x2, y2, color, thickness),
            _line(x2, y2, x2 - direction * head, y2 - head // 2, color, thickness),
            _line(x2, y2, x2 - direction * head, y2 + head // 2, color, thickness),
        ]
    direction = 1 if y2 >= y1 else -1
    return [
        _line(x1, y1, x2, y2, color, thickness),
        _line(x2, y2, x2 - head // 2, y2 - direction * head, color, thickness),
        _line(x2, y2, x2 + head // 2, y2 - direction * head, color, thickness),
    ]


def _motif_ops(motifs: list[str], accent: str, accent_2: str, text: str, width: int, height: int) -> list[PatternOp]:
    ops: list[PatternOp] = []
    muted = _alpha(text, 0.090)
    main = _alpha(accent, 0.125)
    second = _alpha(accent_2, 0.115)
    for motif in motifs[:3]:
        if motif == "temple_gate":
            x, y = width - 375, 688
            ops.extend([
                _line(x + 24, y + 22, x + 268, y + 22, main, 8),
                _line(x + 58, y + 58, x + 235, y + 58, _alpha(accent, 0.080), 5),
                _roundrect(x + 62, y + 58, 28, 165, second, 8),
                _roundrect(x + 206, y + 58, 28, 165, second, 8),
                _line(x + 28, y + 52, x + 270, y + 52, muted, 4),
                _line(x + 68, y + 222, x + 228, y + 222, main, 4),
            ])
        elif motif == "mother_shadow":
            x, y = width - 248, 704
            ops.extend([
                _circle(x, y, 34, _alpha(text, 0.065)),
                _roundrect(x - 40, y + 38, 82, 150, _alpha(text, 0.052), 38),
                _circle(x - 54, y + 122, 16, _alpha(accent, 0.070)),
                _line(x - 46, y + 132, x - 92, y + 182, _alpha(accent, 0.070), 4),
            ])
        elif motif == "autumn_path":
            ops.extend([
                _diag(105, height - 184, 420, height - 256, second, 5, 40),
                _diag(130, height - 128, 468, height - 178, _alpha(accent, 0.075), 4, 34),
            ])
            for idx, (cx, cy) in enumerate(((150, 850), (220, 828), (306, 818), (392, 790), (482, 770))):
                ops.append(_roundrect(cx - 15, cy - 8, 30, 16, _alpha("#d97706", 0.075 + idx * 0.006), 9))
                ops.append(_line(cx - 11, cy + 8, cx + 12, cy - 8, _alpha("#92400e", 0.060), 2))
        elif motif == "wheel_track":
            for x in (160, 228):
                ops.append(_diag(x, 875, x + 235, 812, _alpha(text, 0.070), 3, 26))
            for idx in range(5):
                ops.append(_circle(168 + idx * 48, 868 - idx * 12, 6, _alpha(accent, 0.095)))
                ops.append(_circle(236 + idx * 48, 851 - idx * 12, 6, _alpha(accent, 0.095)))
        elif motif == "sea_waves":
            for row in range(3):
                y = 792 + row * 34
                for col in range(5):
                    x = width - 500 + col * 74
                    ops.append(_diag(x, y, x + 36, y - 16, _alpha(accent_2, 0.105), 4, 8))
                    ops.append(_diag(x + 36, y - 16, x + 72, y, _alpha(accent_2, 0.105), 4, 8))
            ops.extend([
                _diag(width - 378, 700, width - 300, 620, _alpha(accent, 0.090), 4, 20),
                _diag(width - 300, 620, width - 214, 710, _alpha(accent, 0.090), 4, 20),
            ])
        elif motif == "quote_scroll":
            x, y = width - 352, 112
            ops.extend([
                _roundrect(x, y, 226, 64, _alpha(accent, 0.055), 18),
                _rect(x + 36, y + 22, 148, 4, main),
                _rect(x + 36, y + 42, 110, 4, _alpha(accent_2, 0.095)),
                _circle(x + 18, y + 32, 16, _alpha(accent, 0.080)),
                _circle(x + 208, y + 32, 16, _alpha(accent, 0.080)),
            ])
        elif motif == "writing_brush":
            ops.extend([
                _diag(width - 326, 726, width - 204, 875, _alpha(text, 0.100), 7, 26),
                _roundrect(width - 222, 856, 52, 18, _alpha(accent, 0.115), 9),
                _diag(width - 356, 900, width - 236, 910, _alpha(accent, 0.065), 5, 18),
            ])
        elif motif == "friction_surface":
            x, y = width - 470, 800
            ops.extend([
                _roundrect(x + 84, y - 72, 176, 70, _alpha(accent, 0.082), 16),
                _line(x, y, x + 372, y, _alpha(text, 0.140), 5),
            ])
            for idx in range(9):
                ops.append(_diag(x + idx * 42, y + 22, x + idx * 42 + 22, y + 4, _alpha(text, 0.105), 3, 6))
            ops.extend(_arrow_ops(x + 270, y - 36, x + 372, y - 36, _alpha(accent, 0.150), 5))
            ops.extend(_arrow_ops(x + 80, y - 36, x - 10, y - 36, _alpha("#dc2626", 0.115), 5))
            ops.append(_text("f", x + 22, y - 78, 28, _alpha("#dc2626", 0.120)))
        elif motif == "force_vectors":
            x, y = width - 330, 210
            ops.extend(_arrow_ops(x, y + 90, x, y - 55, second, 5))
            ops.extend(_arrow_ops(x, y + 90, x + 152, y + 90, main, 5))
            ops.extend(_arrow_ops(x, y + 90, x, y + 218, _alpha("#dc2626", 0.095), 5))
            ops.append(_text("N", x + 14, y - 64, 28, second))
            ops.append(_text("F", x + 162, y + 68, 28, main))
        elif motif == "dynamometer":
            x, y = width - 270, 690
            ops.extend([
                _roundrect(x, y, 54, 190, _alpha(accent, 0.080), 26),
                _circle(x + 27, y + 36, 15, _alpha(accent_2, 0.115)),
                _line(x + 27, y + 62, x + 27, y + 150, _alpha(text, 0.105), 3),
                _line(x + 27, y + 190, x + 27, y + 246, _alpha(text, 0.100), 4),
                _roundrect(x - 30, y + 246, 114, 18, _alpha(accent_2, 0.075), 9),
            ])
        elif motif == "radical_mark":
            x, y = width - 398, 162
            ops.extend([
                _line(x, y + 62, x + 42, y + 122, _alpha(accent, 0.135), 7),
                _line(x + 42, y + 122, x + 88, y, _alpha(accent, 0.135), 7),
                _line(x + 88, y, x + 260, y, _alpha(accent, 0.135), 7),
                _text("9", x + 132, y + 20, 42, _alpha(accent_2, 0.125)),
            ])
        elif motif == "coordinate_curve":
            x, y = 142, 758
            ops.extend([
                _line(x, y, x + 330, y, muted, 4),
                _line(x + 70, y + 96, x + 70, y - 150, muted, 4),
                _diag(x + 86, y + 52, x + 338, y - 116, main, 5, 34),
            ])
        elif motif == "geometry_triangle":
            x, y = width - 400, 742
            ops.extend([
                _line(x, y + 148, x + 210, y + 148, main, 5),
                _line(x, y + 148, x + 72, y, second, 5),
                _line(x + 72, y, x + 210, y + 148, muted, 5),
            ])
        elif motif == "number_line":
            x, y = 132, 850
            ops.append(_line(x, y, x + 390, y, muted, 4))
            for idx in range(6):
                tx = x + 42 + idx * 62
                ops.append(_line(tx, y - 16, tx, y + 16, muted, 3))
            ops.extend(_arrow_ops(x + 130, y - 44, x + 286, y - 44, main, 4))
        elif motif == "archive_timeline":
            ops.append(_line(160, 850, 560, 850, main, 4))
            for x in (210, 310, 410, 510):
                ops.append(_circle(x, 850, 8, second))
                ops.append(_line(x, 815, x, 885, _alpha(accent, 0.060), 2))
        elif motif == "map_route":
            pts = [(1450, 170), (1530, 240), (1608, 202), (1705, 315)]
            for a, b in zip(pts, pts[1:]):
                ops.append(_diag(a[0], a[1], b[0], b[1], main, 4, 14))
            for x, y in pts:
                ops.append(_circle(x, y, 10, second))
        elif motif == "seal_document":
            x, y = width - 304, 120
            ops.extend([
                _roundrect(x, y, 116, 116, _alpha("#b91c1c", 0.070), 58),
                _roundrect(x + 28, y + 28, 60, 60, _alpha("#b91c1c", 0.095), 8),
            ])
        elif motif == "contour_map":
            for idx, (x, y, w, h) in enumerate(((1440, 145, 280, 124), (1480, 188, 205, 96), (1512, 228, 140, 68))):
                ops.append(_roundrect(x, y, w, h, _alpha(accent_2, 0.052 + idx * 0.012), 48))
        elif motif == "climate_chart":
            x, y = width - 408, 770
            ops.append(_line(x, y + 112, x + 260, y + 112, muted, 3))
            ops.append(_line(x, y + 112, x, y, muted, 3))
            for idx, h in enumerate((42, 68, 92, 56, 76)):
                ops.append(_rect(x + 34 + idx * 42, y + 112 - h, 22, h, _alpha(accent, 0.080)))
        elif motif == "map_pin":
            x, y = width - 240, 180
            ops.append(_circle(x, y, 34, _alpha(accent, 0.095)))
            ops.append(_circle(x, y, 13, _alpha(accent_2, 0.125)))
            ops.append(_diag(x, y + 32, x - 28, y + 92, _alpha(accent, 0.090), 6, 16))
            ops.append(_diag(x, y + 32, x + 28, y + 92, _alpha(accent, 0.090), 6, 16))
        elif motif == "molecule_reaction":
            nodes = [(1520, 170), (1615, 222), (1510, 300), (1710, 294)]
            for a, b in ((0, 1), (1, 2), (1, 3)):
                ops.append(_diag(nodes[a][0], nodes[a][1], nodes[b][0], nodes[b][1], main, 4, 14))
            for idx, (cx, cy) in enumerate(nodes):
                ops.append(_circle(cx, cy, 16 if idx == 1 else 12, second if idx % 2 else main))
        elif motif == "test_tube":
            x, y = width - 250, 676
            ops.extend([
                _roundrect(x, y, 44, 190, _alpha(accent, 0.072), 20),
                _roundrect(x + 76, y + 28, 44, 162, _alpha(accent_2, 0.072), 20),
                _line(x - 18, y, x + 62, y, muted, 4),
                _line(x + 58, y + 28, x + 138, y + 28, muted, 4),
            ])
        elif motif == "cell_structure":
            x, y = width - 250, 240
            ops.append(_circle(x, y, 82, _alpha(accent_2, 0.060)))
            ops.append(_circle(x + 18, y - 8, 28, _alpha(accent, 0.092)))
        elif motif == "dna_chain":
            for idx in range(6):
                x = 150 + idx * 32
                y1 = 780 + (idx % 2) * 38
                y2 = 818 - (idx % 2) * 38
                ops.append(_diag(x, y1, x + 28, y2, main, 3, 8))
                ops.append(_circle(x, y1, 6, second))
                ops.append(_circle(x + 28, y2, 6, main))
        elif motif == "leaf_process":
            ops.extend([
                _roundrect(142, 790, 120, 54, _alpha(accent_2, 0.070), 34),
                _diag(150, 820, 255, 812, _alpha(accent_2, 0.090), 3, 18),
            ])
        elif motif == "algorithm_flow":
            x, y = width - 384, 160
            for idx in range(3):
                ops.append(_roundrect(x + idx * 118, y + idx * 48, 86, 42, _alpha(accent, 0.065), 10))
                if idx < 2:
                    ops.extend(_arrow_ops(x + idx * 118 + 86, y + idx * 48 + 21, x + (idx + 1) * 118, y + (idx + 1) * 48 + 21, second, 3))
        elif motif == "data_nodes":
            pts = [(140, 810), (250, 760), (330, 840), (430, 792)]
            for a, b in zip(pts, pts[1:]):
                ops.append(_diag(a[0], a[1], b[0], b[1], main, 3, 12))
            for x, y in pts:
                ops.append(_roundrect(x - 10, y - 10, 20, 20, second, 6))
        elif motif == "trend_chart":
            pts = [(145, 870), (245, 824), (342, 846), (456, 772)]
            for a, b in zip(pts, pts[1:]):
                ops.append(_diag(a[0], a[1], b[0], b[1], main, 5, 18))
            for x, y in pts:
                ops.append(_circle(x, y, 8, second))
        elif motif == "product_card":
            ops.extend([
                _roundrect(width - 350, 720, 230, 128, _alpha(accent, 0.055), 18),
                _roundrect(width - 326, 744, 64, 64, _alpha(accent_2, 0.075), 14),
                _rect(width - 238, 750, 78, 5, main),
                _rect(width - 238, 776, 112, 5, second),
                _rect(width - 238, 802, 84, 5, _alpha(text, 0.070)),
            ])
    return ops


def background_pattern_for_subject(subject: str = "general", family: str = "general", scene: str = "concept") -> dict[str, Any]:
    profile = dict(PATTERN_BY_SUBJECT.get(subject) or PATTERN_BY_SUBJECT.get(family) or PATTERN_BY_SUBJECT["general"])
    profile["subject"] = subject or "general"
    profile["scene"] = scene or "concept"
    return profile


def background_pattern_for_framework(framework: dict[str, Any] | None) -> dict[str, Any]:
    framework = framework or {}
    return background_pattern_for_subject(
        str(framework.get("subject") or "general"),
        str(framework.get("family") or "general"),
        str(framework.get("scene") or "concept"),
    )


def background_layers_for_framework(
    framework: dict[str, Any] | None,
    *,
    width: int = 1920,
    height: int = 1080,
    variant: int = 0,
) -> list[PatternOp]:
    framework = framework or {}
    subject = str(framework.get("subject") or "general")
    family = str(framework.get("family") or "general")
    scene = str(framework.get("scene") or "concept")
    theme = framework.get("theme") if isinstance(framework.get("theme"), dict) else {}
    accent = _hex(theme.get("accent"), "#2563eb")
    accent_2 = _hex(theme.get("accent_2"), "#16a34a")
    text = _hex(theme.get("text"), "#0f172a")
    muted = "#94a3b8"
    variant = int(variant or 0) % 4
    motifs = [
        str(item)
        for item in (framework.get("content_motifs") or framework.get("motifs") or [])
        if str(item).strip()
    ]
    if not motifs:
        motifs = content_motifs_for_text(subject, scene, "", limit=2)

    ops: list[PatternOp] = []
    pattern = background_pattern_for_subject(subject, family, scene)
    pattern_id = pattern["id"]

    if pattern_id == "literary_paper_branch":
        paper_line = "#c7b69f"
        ops.extend(_paper_texture_ops(width, height, paper_line))
        ops.append(_roundrect(74, 142, 16, 700, _alpha(accent, 0.180), 8))
        ops.append(_roundrect(124, 784, 480, 6, _alpha(accent_2, 0.240), 3))
        ops.append(_roundrect(1340 + variant * 10, 874, 350, 10, _alpha(accent, 0.055), 5))
        return ops

    if pattern_id == "archive_timeline":
        ops.extend(_paper_texture_ops(width, height, "#b99163"))
        ops.extend(_corner_ops(150, 135, 1, 1, _alpha(accent, 0.150), _alpha(accent_2, 0.090)))
        ops.append(_roundrect(width - 318, 132, 170, 88, _alpha(accent, 0.075), 4))
        ops.append(_rect(width - 290, 170, 112, 5, _alpha(accent, 0.190)))
        ops.append(_line(185, 865, width - 260, 865, _alpha(accent, 0.140), 4))
        for idx, x in enumerate((310, 565, 820, 1075, 1330, 1585)):
            ops.append(_circle(x, 865, 9, _alpha(accent_2, 0.180)))
            ops.append(_rect(x, 820 - (idx % 2) * 38, 2, 80, _alpha(accent, 0.090)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "civic_document":
        ops.extend(_paper_texture_ops(width, height, "#a8a29e"))
        ops.append(_roundrect(width - 300, 116, 128, 128, _alpha(accent, 0.075), 64))
        ops.append(_roundrect(width - 272, 144, 72, 72, _alpha(accent, 0.105), 36))
        for y in (212, 280, 348, 416):
            ops.append(_rect(122, y, 330, 3, _alpha(accent_2, 0.070)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "atlas_contour":
        ops.extend(_grid_ops(width, height, accent_2, 220, 160, 0.048))
        for idx, (x, y, w, h) in enumerate(((1480, 130, 250, 130), (1530, 175, 290, 160), (1425, 222, 245, 130))):
            ops.append(_roundrect(x, y, w, h, _alpha(accent, 0.045 + idx * 0.014), 44))
        ops.append(_line(155, 812, 560, 812, _alpha(accent, 0.130), 4))
        ops.append(_line(240, 750, 240, 912, _alpha(accent_2, 0.105), 4))
        ops.append(_circle(240, 812, 17, _alpha(accent, 0.145)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "math_graph_paper":
        ops.extend(_grid_ops(width, height, accent, 160, 120, 0.052))
        ops.append(_line(145, 820, 610, 820, _alpha(text, 0.130), 4))
        ops.append(_line(226, 635, 226, 910, _alpha(text, 0.125), 4))
        ops.append(_diag(240, 800, 570, 670, _alpha(accent_2, 0.135), 5, 36))
        ops.append(_text("y=f(x)", width - 330, 142, 38, _alpha(accent, 0.155)))
        ops.append(_text("a^2+b^2", width - 420, 846, 32, _alpha(accent_2, 0.115)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "physics_vector_lab":
        ops.extend(_grid_ops(width, height, accent_2, 210, 160, 0.045))
        ops.append(_diag(122, 875, 430, 718, _alpha(text, 0.135), 7, 42))
        ops.append(_line(510, 268, 735, 268, _alpha(accent, 0.155), 7))
        ops.append(_line(735, 268, 700, 244, _alpha(accent, 0.155), 7))
        ops.append(_line(735, 268, 700, 292, _alpha(accent, 0.155), 7))
        ops.append(_line(1510, 220, 1510, 390, _alpha(accent_2, 0.130), 6))
        ops.append(_line(1510, 390, 1488, 352, _alpha(accent_2, 0.130), 6))
        ops.append(_line(1510, 390, 1532, 352, _alpha(accent_2, 0.130), 6))
        ops.append(_text("F", 756, 240, 32, _alpha(accent, 0.170)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "chemistry_molecule_lab":
        ops.extend(_grid_ops(width, height, accent_2, 230, 170, 0.040))
        nodes = [(1568, 160), (1660, 214), (1550, 298), (1745, 304), (1468, 232)]
        for a, b in ((0, 1), (1, 2), (1, 3), (0, 4)):
            ops.append(_diag(nodes[a][0], nodes[a][1], nodes[b][0], nodes[b][1], _alpha(accent, 0.115), 4, 18))
        for idx, (cx, cy) in enumerate(nodes):
            ops.append(_circle(cx, cy, 18 if idx == 1 else 13, _alpha(accent_2 if idx % 2 else accent, 0.145)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "biology_cell_lab":
        ops.extend(_grid_ops(width, height, accent_2, 240, 180, 0.038))
        for idx, (cx, cy, r) in enumerate(((1545, 196, 92), (1680, 312, 58), (220, 850, 76))):
            ops.append(_circle(cx, cy, r, _alpha(accent_2, 0.055 + idx * 0.012)))
            ops.append(_circle(cx + 16, cy - 8, max(14, r // 4), _alpha(accent, 0.090)))
        ops.append(_diag(126, 154, 320, 258, _alpha(accent, 0.100), 4, 28))
        ops.append(_diag(126, 214, 320, 318, _alpha(accent_2, 0.090), 4, 28))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "computer_circuit_grid":
        ops.extend(_grid_ops(width, height, accent, 190, 150, 0.042))
        nodes = [(1430, 150), (1535, 150), (1535, 250), (1660, 250), (1660, 358), (1780, 358)]
        for a, b in zip(nodes, nodes[1:]):
            ops.append(_line(a[0], a[1], b[0], b[1], _alpha(accent, 0.130), 4))
        for cx, cy in nodes:
            ops.append(_roundrect(cx - 10, cy - 10, 20, 20, _alpha(accent_2, 0.170), 6))
        ops.append(_text("01", 172, 812, 36, _alpha(accent, 0.115)))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    if pattern_id == "business_dashboard_grid":
        ops.extend(_grid_ops(width, height, muted, 260, 190, 0.035))
        for idx, (x, y, w, h) in enumerate(((1450, 142, 260, 112), (1510, 290, 300, 142), (126, 825, 340, 96))):
            ops.append(_roundrect(x, y, w, h, _alpha(accent if idx != 1 else accent_2, 0.060), 18))
            ops.append(_rect(x + 28, y + 32, int(w * 0.62), 6, _alpha(accent, 0.110)))
            ops.append(_rect(x + 28, y + 60, int(w * 0.42), 6, _alpha(accent_2, 0.095)))
        ops.append(_diag(152, 878, 410, 832, _alpha(accent_2, 0.135), 5, 28))
        ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
        return ops

    ops.append(_circle(width - 205, 205, 132, _alpha(accent, 0.060)))
    ops.append(_circle(210, 865, 102, _alpha(accent_2, 0.052)))
    ops.append(_roundrect(118, 826, 420, 8, _alpha(accent, 0.100), 4))
    ops.extend(_motif_ops(motifs, accent, accent_2, text, width, height))
    return ops
