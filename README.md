# EndStone ARC Inventory / 弧光背包管理器

[![Codacy Grade](https://app.codacy.com/project/badge/Grade/4c63155069c84452b4854f597cd258a7)](https://app.codacy.com/gh/ARC-Minecraft/EndstoneMC-ARC-Inventory-Manager/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![版本](https://img.shields.io/badge/版本-0.1.3-blue.svg)](https://github.com/ARC-Minecraft/EndstoneMC-ARC-Inventory-Manager)
[![EndStone](https://img.shields.io/badge/EndStone-0.10+-green.svg)](https://github.com/EndstoneMC/endstone)

弧光系列共享背包工具插件。统一处理玩家背包的读取、匹配、扣除与发放（含附魔、Lore、Bedrock NBT），供按钮商店、成就等插件复用，避免各插件各写一套易踩坑的逻辑。

## 命名约定

| 项 | 值 |
|---|---|
| 包名 | `endstone_arc_inventory` |
| Plugin id | `arc_inventory` |
| 显示名 | 弧光背包管理器 |

本插件**无独立数据目录**（无配置文件）；纯 API 服务。

## 功能特性

- **列出背包**：槽位、类型 ID、显示名、数量、data、附魔、Lore；复杂物品可带 `nbt_b64`
- **匹配检查**：`has_item` — 类型 / 数量 / data；有 `nbt_b64` 时按完整 NBT 比对，否则比附魔与 Lore
- **扣除物品**：`remove_item` — 数量不足则失败且不改动背包
- **发放物品**：`give_item` / `give_item_count` — 支持附魔、Lore、NBT 还原；背包满时返回实际入包数量
- **附魔读取**：不直接遍历 `ItemMeta.enchants`（避免 unhashable），改用 `get_enchant_level` 按已知附魔 ID 查询

## 安装

1. 将 `endstone_arc_inventory-*.whl` 放入服务器 `plugins/`
2. 重启服务器
3. 其它插件通过 `server.get_plugin("arc_inventory")` 调用 API

依赖本插件的系列插件（如弧光按钮商店）应同时安装本 wheel。

### 本地构建

```bash
pip install build
python -m build
# dist/endstone_arc_inventory-<version>-py2.py3-none-any.whl
```

## 其它插件如何调用

```python
inv = self.server.plugin_manager.get_plugin("arc_inventory")
if inv is None:
    self.logger.error("需要安装弧光背包管理器 (arc_inventory)")
    return

# 列出背包
items = inv.api_get_inventory_items(player)
# items[i] ≈ {
#   "type": "minecraft:diamond_sword",
#   "name": "...",
#   "count": 1,
#   "data": 0,
#   "enchants": {"minecraft:sharpness": 5},
#   "lore": ["..."],
#   "slot_index": 0,
#   # 可选 "nbt_b64": "..."
# }

item_info = {"type": "minecraft:diamond", "count": 3, "data": 0}

if inv.api_has_item(player, item_info):
    inv.api_remove_item(player, item_info)

# 发放：足额与否
ok = inv.api_give_item(player, item_info)

# 或拿实际数量（背包满时可能小于 count）
given = inv.api_give_item_count(player, {"type": "minecraft:apple", "count": 64})

# 高级：直接拿 InventoryManager（与按钮商店旧用法一致）
mgr = inv.api_get_inventory_manager()
```

### `item_info` 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 物品类型 ID，如 `minecraft:diamond` |
| `count` | 是 | 数量 |
| `data` | 否 | 默认 `0` |
| `enchants` | 否 | `{附魔ID: 等级}` |
| `lore` | 否 | 字符串列表 |
| `nbt_b64` | 否 | 完整用户 NBT 的 Base64；有则匹配/发放以此为准 |

## API 一览

| 方法 | 返回 | 说明 |
|------|------|------|
| `api_get_inventory_items(player)` | `list[dict]` | 背包物品列表 |
| `api_has_item(player, item_info)` | `bool` | 是否够量匹配 |
| `api_remove_item(player, item_info)` | `bool` | 扣除 |
| `api_give_item(player, item_info)` | `bool` | 是否足额发放 |
| `api_give_item_count(player, item_info)` | `int` | 实际发放数量 |
| `api_get_inventory_manager()` | `InventoryManager \| None` | 底层管理器 |

## 与弧光系列

| 插件 | 关系 |
|------|------|
| 弧光按钮商店 | **硬依赖**本插件进行交易扣物/发物（已无内嵌回退） |
| 弧光核心 / 成就等 | 需要精确背包操作时可同样依赖本插件 |

## 更新日志

### v0.1.3
确认并整理近期发放/读取相关修复，供按钮商店等插件升级对照：

- **启用顺序**：在 `on_load` 即创建 `InventoryManager`，并 `load_before` 按钮商店，避免商店先启用时拿到空管理器
- **不可堆叠数量**：发放不再硬编码 64，改为读取 `ItemStack.max_stack_size`；镐等 max=1 的物品会逐个入包，删店/购买不会少给
- **附魔书 NBT**：完整用户 NBT 还原失败时回退到附魔/Lore（`add_enchant(..., force=True)`）；也可从 NBT `ench` 列表解析附魔，避免买到无属性的附魔书

### v0.1.2
- 更早初始化 `InventoryManager`（`on_load`），并声明 `load_before` 按钮商店，避免其它插件启用时拿到空管理器

### v0.1.1
- 修复发放时硬编码堆叠 64：改为读取 `ItemStack.max_stack_size`，镐等不可堆叠物品会逐个发放，避免删店/购买数量丢失
- 修复附魔书等 NBT 还原失败后因 `if/elif` 无法回退到附魔/Lore 的问题；`add_enchant` 使用 `force=True`
- 读取附魔时不再因 `has_enchants=False` 提前放弃；可从 NBT `ench` 列表补充解析

## 许可证

见 [LICENSE](LICENSE)。
