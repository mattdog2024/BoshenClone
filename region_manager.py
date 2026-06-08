"""
region_manager.py
==================
多周期窗格区域管理器。

背景：用户更换了期货软件，新界面是“多周期”页面 —— 一个屏幕上同时显示 6 个不同周期
（如 周线 / 日线 / 2小时 / 30分钟 / 5分钟 / 3分钟）的 K 线图，呈 2 行 3 列网格排列。

本模块负责：
1. 维护一组“周期区域”（Region）。每个区域 = 屏幕上的一个矩形 + 一个周期名字。
2. 提供命中判定（给定一个屏幕坐标，返回它落在哪个区域）。
3. 提供“当前激活区域”的概念（用户框选某个周期后，新的测量都归到这个区域）。
4. 把区域定义持久化到本地文件，下次启动还能恢复。

每个区域的测量线由 overlay 自己保存（drawings 里带 region_id 标签），
本模块只负责“区域”本身的几何与元数据，不直接保存测量线，
这样职责清晰、互不干扰。
"""

import json
import os
import uuid

from PySide6.QtCore import QRect, QPoint


class Region:
    """一个周期窗格区域。"""

    def __init__(self, rect: QRect, name: str = "", region_id: str = None):
        # region_id 用于在 overlay 的 drawings 里做关联标签，保证唯一
        self.id = region_id or uuid.uuid4().hex[:8]
        self.rect = QRect(rect)  # 屏幕逻辑坐标下的矩形
        self.name = name or "周期"

    # ---- 几何相关 ----
    def contains(self, point: QPoint) -> bool:
        return self.rect.contains(point)

    def center(self) -> QPoint:
        return self.rect.center()

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "x": self.rect.x(),
            "y": self.rect.y(),
            "w": self.rect.width(),
            "h": self.rect.height(),
        }

    @staticmethod
    def from_dict(d: dict) -> "Region":
        rect = QRect(int(d.get("x", 0)), int(d.get("y", 0)),
                     int(d.get("w", 0)), int(d.get("h", 0)))
        return Region(rect, name=d.get("name", "周期"), region_id=d.get("id"))


class RegionManager:
    """管理所有周期区域及其激活状态。"""

    def __init__(self, filename: str = "regions.json"):
        self.filename = filename
        self.regions = []           # List[Region]
        self.active_region_id = None  # 当前被框选/激活的区域 id
        self.load()

    # ------------------------------------------------------------------
    # 增删查
    # ------------------------------------------------------------------
    def add_region(self, rect: QRect, name: str = "") -> Region:
        """
        新增一个区域。如果不给名字，自动用“周期1/周期2...”命名。
        新增后自动设为激活区域。
        """
        if not name:
            name = f"周期{len(self.regions) + 1}"
        region = Region(rect, name=name)
        self.regions.append(region)
        self.active_region_id = region.id
        self.save()
        return region

    def remove_region(self, region_id: str):
        self.regions = [r for r in self.regions if r.id != region_id]
        if self.active_region_id == region_id:
            self.active_region_id = self.regions[-1].id if self.regions else None
        self.save()

    def clear_regions(self):
        self.regions.clear()
        self.active_region_id = None
        self.save()

    def get_region(self, region_id: str):
        for r in self.regions:
            if r.id == region_id:
                return r
        return None

    def get_active_region(self):
        return self.get_region(self.active_region_id)

    # ------------------------------------------------------------------
    # 命中判定
    # ------------------------------------------------------------------
    def region_at(self, point: QPoint):
        """
        返回包含给定点的区域。如果多个区域重叠，返回最后一个（最上层）。
        没有命中返回 None。
        """
        hit = None
        for r in self.regions:
            if r.contains(point):
                hit = r
        return hit

    def set_active_by_point(self, point: QPoint):
        """根据点击位置激活对应区域，返回该区域（或 None）。"""
        r = self.region_at(point)
        if r:
            self.active_region_id = r.id
            self.save()
        return r

    def set_active(self, region_id: str):
        if self.get_region(region_id):
            self.active_region_id = region_id
            self.save()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.regions = [Region.from_dict(d) for d in data.get("regions", [])]
            self.active_region_id = data.get("active_region_id")
            # 校验激活 id 仍然存在
            if not self.get_region(self.active_region_id):
                self.active_region_id = self.regions[-1].id if self.regions else None
        except Exception as e:
            print(f"[RegionManager] load error: {e}")

    def save(self):
        try:
            data = {
                "regions": [r.to_dict() for r in self.regions],
                "active_region_id": self.active_region_id,
            }
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[RegionManager] save error: {e}")
