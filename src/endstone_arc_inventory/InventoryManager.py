# -*- coding: utf-8 -*-
"""
背包管理类：统一负责玩家背包的读取、匹配、移除与发放。
复用附魔/洛尔等 Endstone API 的转换与比较逻辑，便于维护与扩展。
Endstone ItemMeta.enchants 返回 dict[Enchantment, int]，键不可哈希会报错，
故通过 get_enchant_level(id: str) 逐个查询已知附魔 id 获取等级。
"""
import base64
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

# Endstone 已知附魔 id 列表（minecraft:xxx），用于 get_enchant_level 逐个查询，避免访问 .enchants
_ENCHANT_IDS: List[str] = []

# 护甲 / 副手属性名（PlayerInventory）
ARMOR_ATTRS: Tuple[str, ...] = (
    "helmet",
    "chestplate",
    "leggings",
    "boots",
    "item_in_off_hand",
)


def _normalize_enchant_id(eid: str) -> str:
    """统一为 minecraft:xxx 格式，兼容旧数据中的短 id。"""
    if not eid:
        return eid
    if eid.startswith("minecraft:"):
        return eid
    return "minecraft:" + eid.replace(" ", "_").lower()


def _build_enchant_ids() -> List[str]:
    """从 endstone.enchantments.Enchantment 收集所有附魔字符串 id（仅执行一次）。"""
    global _ENCHANT_IDS
    if _ENCHANT_IDS:
        return _ENCHANT_IDS
    try:
        from endstone.enchantments import Enchantment
        for name in dir(Enchantment):
            if name.isupper():
                val = getattr(Enchantment, name, None)
                if isinstance(val, str) and val.startswith("minecraft:"):
                    _ENCHANT_IDS.append(val)
    except Exception:
        pass
    if not _ENCHANT_IDS:
        _ENCHANT_IDS = [
            "minecraft:aqua_affinity", "minecraft:bane_of_arthropods",
            "minecraft:blast_protection", "minecraft:breach", "minecraft:channeling",
            "minecraft:binding", "minecraft:vanishing", "minecraft:density",
            "minecraft:depth_strider", "minecraft:efficiency", "minecraft:feather_falling",
            "minecraft:fire_aspect", "minecraft:fire_protection", "minecraft:flame",
            "minecraft:frost_walker", "minecraft:impaling", "minecraft:infinity",
            "minecraft:knockback", "minecraft:looting", "minecraft:loyalty",
            "minecraft:luck_of_the_sea", "minecraft:lure", "minecraft:mending",
            "minecraft:multishot", "minecraft:piercing", "minecraft:power",
            "minecraft:projectile_protection", "minecraft:protection", "minecraft:punch",
            "minecraft:quick_charge", "minecraft:respiration", "minecraft:riptide",
            "minecraft:sharpness", "minecraft:silk_touch", "minecraft:smite",
            "minecraft:soul_speed", "minecraft:swift_sneak", "minecraft:thorns",
            "minecraft:unbreaking", "minecraft:wind_burst",
        ]
    return _ENCHANT_IDS


class InventoryManager:
    """
    专门负责玩家背包物品管理的类。
    依赖插件实例以使用 _safe_log 与 server（如语言翻译）。
    """

    def __init__(self, plugin: Any):
        """
        :param plugin: 插件实例，需提供 _safe_log(level, message) 与 server
        """
        self._plugin = plugin
        self._server = getattr(plugin, "server", None)

    def _log(self, level: str, message: str) -> None:
        if hasattr(self._plugin, "_safe_log") and self._plugin._safe_log:
            self._plugin._safe_log(level, message)
        else:
            print(f"[{level.upper()}] {message}")

    def _serialize_item_nbt(self, item_stack: Any) -> Optional[str]:
        """
        将物品用户数据序列化为 Base64（Bedrock little-endian），用于完整还原附魔书、药水等 ItemMeta 无法表达的标签。
        """
        try:
            if not item_stack:
                return None
            nbt_compound = getattr(item_stack, "nbt", None)
            if nbt_compound is None:
                return None
            keys_fn = getattr(nbt_compound, "keys", None)
            if callable(keys_fn) and not list(keys_fn()):
                return None
            raw = nbt_compound.dump(byte_order="little")
            if not raw:
                return None
            return base64.b64encode(raw).decode("ascii")
        except Exception:
            return None

    def _get_item_enchants(self, item_stack: Any) -> Dict[str, int]:
        """
        从 ItemStack 安全读取附魔信息（str->int）。
        不访问 ItemMeta.enchants（会触发 unhashable），改用 get_enchant_level(id) 逐个查询。
        附魔书等物品可能 has_enchants=False 但仍能按 id 读到等级，故不提前因 has_enchants 返回空。
        """
        result: Dict[str, int] = {}
        if not item_stack:
            return result
        meta = getattr(item_stack, "item_meta", None)
        get_level = getattr(meta, "get_enchant_level", None) if meta is not None else None
        if callable(get_level):
            try:
                for enchant_id in _build_enchant_ids():
                    try:
                        level = get_level(enchant_id)
                        if level and int(level) > 0:
                            result[enchant_id] = int(level)
                    except Exception:
                        continue
            except Exception as enc_e:
                self._log(
                    "warning",
                    f"[ARCInventory] Get enchants (get_enchant_level) failed: {enc_e}\n{traceback.format_exc()}",
                )
        if not result:
            result = self._get_enchants_from_nbt(item_stack)
        return result

    def _get_enchants_from_nbt(self, item_stack: Any) -> Dict[str, int]:
        """从用户 NBT 的 ench 列表尽量解析附魔（Bedrock 附魔书主要靠此）。"""
        result: Dict[str, int] = {}
        try:
            nbt = getattr(item_stack, "nbt", None)
            if nbt is None:
                return result
            ench = None
            if hasattr(nbt, "get"):
                try:
                    ench = nbt.get("ench")
                except Exception:
                    ench = None
            if ench is None:
                try:
                    ench = nbt["ench"]
                except Exception:
                    return result
            if ench is None:
                return result
            # ListTag / list
            try:
                entries = list(ench)
            except Exception:
                return result
            id_by_num = {
                # Bedrock legacy numeric ids commonly seen on enchanted books
                0: "minecraft:protection",
                1: "minecraft:fire_protection",
                2: "minecraft:feather_falling",
                3: "minecraft:blast_protection",
                4: "minecraft:projectile_protection",
                5: "minecraft:thorns",
                6: "minecraft:respiration",
                7: "minecraft:depth_strider",
                8: "minecraft:aqua_affinity",
                9: "minecraft:sharpness",
                10: "minecraft:smite",
                11: "minecraft:bane_of_arthropods",
                12: "minecraft:knockback",
                13: "minecraft:fire_aspect",
                14: "minecraft:looting",
                15: "minecraft:efficiency",
                16: "minecraft:silk_touch",
                17: "minecraft:unbreaking",
                18: "minecraft:fortune",
                19: "minecraft:power",
                20: "minecraft:punch",
                21: "minecraft:flame",
                22: "minecraft:infinity",
                23: "minecraft:luck_of_the_sea",
                24: "minecraft:lure",
                25: "minecraft:frost_walker",
                26: "minecraft:mending",
                27: "minecraft:binding",
                28: "minecraft:vanishing",
                29: "minecraft:impaling",
                30: "minecraft:riptide",
                31: "minecraft:loyalty",
                32: "minecraft:channeling",
                33: "minecraft:multishot",
                34: "minecraft:piercing",
                35: "minecraft:quick_charge",
                36: "minecraft:soul_speed",
                37: "minecraft:swift_sneak",
            }
            for entry in entries:
                try:
                    eid_raw = None
                    lvl_raw = None
                    if hasattr(entry, "get"):
                        eid_raw = entry.get("id")
                        lvl_raw = entry.get("lvl")
                    if eid_raw is None:
                        try:
                            eid_raw = entry["id"]
                        except Exception:
                            continue
                    if lvl_raw is None:
                        try:
                            lvl_raw = entry["lvl"]
                        except Exception:
                            continue
                    # IntTag / ShortTag 等可能包一层 .value
                    if hasattr(eid_raw, "value"):
                        eid_raw = eid_raw.value
                    if hasattr(lvl_raw, "value"):
                        lvl_raw = lvl_raw.value
                    level = int(lvl_raw)
                    if level <= 0:
                        continue
                    if isinstance(eid_raw, str):
                        eid = _normalize_enchant_id(eid_raw)
                    else:
                        eid = id_by_num.get(int(eid_raw))
                    if eid:
                        result[eid] = level
                except Exception:
                    continue
        except Exception:
            return {}
        return result

    def _get_item_lore(self, item_stack: Any) -> List[str]:
        """从 ItemStack 安全读取 Lore。"""
        if not item_stack or not getattr(item_stack, "item_meta", None):
            return []
        if not getattr(item_stack.item_meta, "has_lore", False):
            return []
        try:
            lore = item_stack.item_meta.lore
            return list(lore) if isinstance(lore, list) else []
        except Exception:
            return []

    def _item_stack_matches_info(
        self,
        item_stack: Any,
        required_type: str,
        required_data: int,
        required_enchants: Dict[str, int],
        required_lore: List[str],
        required_nbt_b64: Optional[str] = None,
    ) -> bool:
        """判断单个 ItemStack 是否与 item_info 要求一致（类型、data；若有 nbt_b64 则比对完整 NBT，否则比对附魔与 Lore）。"""
        if not item_stack or not item_stack.type:
            return False
        if item_stack.type.id != required_type or item_stack.data != required_data:
            return False
        if required_nbt_b64:
            serialized = self._serialize_item_nbt(item_stack)
            return serialized is not None and serialized == required_nbt_b64
        item_enchants = self._get_item_enchants(item_stack)
        item_lore = self._get_item_lore(item_stack)
        if required_enchants:
            for eid, level in required_enchants.items():
                key = eid if eid in item_enchants else _normalize_enchant_id(eid)
                if item_enchants.get(key) != level:
                    return False
        if required_lore:
            if len(required_lore) != len(item_lore):
                return False
            for i, line in enumerate(required_lore):
                if i >= len(item_lore) or item_lore[i] != line:
                    return False
        return True

    def _slot_in_range(
        self,
        slot_index: int,
        slot_min: Optional[int],
        slot_max: Optional[int],
    ) -> bool:
        if slot_min is not None and slot_index < int(slot_min):
            return False
        if slot_max is not None and slot_index > int(slot_max):
            return False
        return True

    def _build_item_entry(
        self,
        player: Any,
        item_stack: Any,
        *,
        slot_index: Optional[Union[int, str]] = None,
        armor_slot: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not item_stack or not getattr(item_stack, "type", None):
            return None
        if int(getattr(item_stack, "amount", 0) or 0) <= 0:
            return None
        try:
            item_type_id = item_stack.type.id
            item_type_translation_key = item_stack.type.translation_key
            display_name = item_type_id
            if self._server and hasattr(self._server, "language"):
                try:
                    display_name = self._server.language.translate(
                        item_type_translation_key,
                        None,
                        getattr(player, "locale", None),
                    )
                except Exception:
                    pass
            if item_stack.item_meta and getattr(
                item_stack.item_meta, "has_display_name", False
            ):
                display_name = item_stack.item_meta.display_name
            enchants = self._get_item_enchants(item_stack)
            lore = self._get_item_lore(item_stack)
            nbt_b64 = self._serialize_item_nbt(item_stack)
            entry: Dict[str, Any] = {
                "type": item_type_id,
                "type_translation_key": item_type_translation_key,
                "name": display_name,
                "count": item_stack.amount,
                "data": item_stack.data,
                "enchants": enchants,
                "lore": lore,
            }
            if slot_index is not None:
                entry["slot_index"] = slot_index
            if armor_slot:
                entry["armor_slot"] = armor_slot
            if nbt_b64:
                entry["nbt_b64"] = nbt_b64
            return entry
        except Exception as item_e:
            self._log(
                "warning",
                f"[ARCInventory] item entry build failed: {item_e}\n{traceback.format_exc()}",
            )
            return None

    def get_inventory_items(
        self,
        player: Any,
        *,
        include_armor: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取玩家背包中所有有效物品的列表。
        每项为 dict：type, type_translation_key, name, count, data, enchants, lore, slot_index；
        若物品含完整用户 NBT（如附魔书），另含 nbt_b64（Base64 二进制 NBT）。
        include_armor=True 时追加护甲/副手，带 armor_slot 字段。
        slot_min/slot_max 仅过滤主背包槽位（含端点）。
        """
        items: List[Dict[str, Any]] = []
        try:
            inventory = player.inventory
            for slot_index in range(inventory.size):
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                try:
                    item_stack = inventory.get_item(slot_index)
                except Exception as slot_e:
                    self._log(
                        "warning",
                        f"[ARCInventory] get_item(slot={slot_index}) failed: {slot_e}",
                    )
                    continue
                entry = self._build_item_entry(
                    player, item_stack, slot_index=slot_index
                )
                if entry:
                    items.append(entry)
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        stack = getattr(inventory, attr, None)
                    except Exception:
                        continue
                    entry = self._build_item_entry(
                        player, stack, slot_index=attr, armor_slot=attr
                    )
                    if entry:
                        items.append(entry)
            return items
        except Exception as e:
            self._log(
                "error",
                f"[ARCInventory] Get player inventory error: {str(e)}\n{traceback.format_exc()}",
            )
            return []

    def has_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> bool:
        """检查玩家背包是否拥有至少 item_info 要求数量、类型、data、附魔、Lore 一致的物品。"""
        try:
            required_count = int(item_info.get("count", 0) or 0)
            if required_count <= 0:
                return True
            have = self.count_item(
                player,
                item_info,
                slot_min=slot_min,
                slot_max=slot_max,
                include_armor=include_armor,
            )
            return have >= required_count
        except Exception as e:
            self._log("error", f"[ARCInventory] Player has item check error: {str(e)}")
            return False

    def count_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> int:
        """统计与 item_info 匹配的物品总数（忽略 item_info.count）。"""
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            total_count = 0
            for slot_index in range(inventory.size):
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                total_count += int(getattr(item_stack, "amount", 0) or 0)
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    item_stack = getattr(inventory, attr, None)
                    if not self._item_stack_matches_info(
                        item_stack,
                        required_type,
                        required_data,
                        required_enchants,
                        required_lore,
                        required_nbt_b64,
                    ):
                        continue
                    total_count += int(getattr(item_stack, "amount", 0) or 0)
            return int(total_count)
        except Exception as e:
            self._log("error", f"[ARCInventory] count_item error: {str(e)}")
            return 0

    def remove_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        partial: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> int:
        """
        从玩家背包移除与 item_info 匹配的物品。
        默认不足则不改动并返回 0；partial=True 时尽可能扣除。
        返回实际移除数量（布尔判断仍兼容：>0 为真）。
        """
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_count = int(item_info.get("count", 0) or 0)
            if required_count <= 0:
                return 0
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            have = self.count_item(
                player,
                item_info,
                slot_min=slot_min,
                slot_max=slot_max,
                include_armor=include_armor,
            )
            if not partial and have < required_count:
                return 0
            remaining_to_remove = min(required_count, have)
            if remaining_to_remove <= 0:
                return 0
            removed_total = 0
            slots_to_modify: List[tuple] = []
            for slot_index in range(inventory.size):
                if remaining_to_remove <= 0:
                    break
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                remove_from_slot = min(remaining_to_remove, item_stack.amount)
                slots_to_modify.append((slot_index, item_stack, remove_from_slot))
                remaining_to_remove -= remove_from_slot
            for slot_index, original_stack, remove_count in slots_to_modify:
                new_amount = original_stack.amount - remove_count
                if new_amount <= 0:
                    inventory.set_item(slot_index, None)
                else:
                    original_stack.amount = new_amount
                    inventory.set_item(slot_index, original_stack)
                removed_total += remove_count
            if include_armor and remaining_to_remove > 0:
                for attr in ARMOR_ATTRS:
                    if remaining_to_remove <= 0:
                        break
                    if not hasattr(inventory, attr):
                        continue
                    item_stack = getattr(inventory, attr, None)
                    if not self._item_stack_matches_info(
                        item_stack,
                        required_type,
                        required_data,
                        required_enchants,
                        required_lore,
                        required_nbt_b64,
                    ):
                        continue
                    have_slot = int(getattr(item_stack, "amount", 0) or 0)
                    take = min(have_slot, remaining_to_remove)
                    if take >= have_slot:
                        setattr(inventory, attr, None)
                    else:
                        item_stack.amount = have_slot - take
                        setattr(inventory, attr, item_stack)
                    remaining_to_remove -= take
                    removed_total += take
            return int(removed_total)
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Remove item from player error: {str(e)}"
            )
            return 0

    def give_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> bool:
        """向玩家背包发放物品（类型、数量、data；附魔/Lore/NBT 若 API 支持则应用）。"""
        given = self.give_item_count(
            player,
            item_info,
            slot=slot,
            armor_slot=armor_slot,
            reserved=reserved,
            prefer_end=prefer_end,
        )
        return given >= int(item_info.get("count", 0) or 0)

    def _resolve_max_stack(self, item_stack: Any) -> int:
        """读取物品真实最大堆叠数；镐等工具为 1，不可再硬编码 64。"""
        max_stack = getattr(item_stack, "max_stack_size", None)
        if max_stack is None:
            item_type = getattr(item_stack, "type", None)
            max_stack = getattr(item_type, "max_stack_size", None) if item_type else None
        try:
            max_stack = int(max_stack) if max_stack is not None else 64
        except Exception:
            max_stack = 64
        return max(1, max_stack)

    def _apply_item_meta_extras(self, item_stack: Any, item_info: Dict[str, Any]) -> bool:
        """用 enchants/lore 写入 ItemMeta；附魔使用 force=True，确保附魔书等可写入。"""
        enchants = item_info.get("enchants") or {}
        lore = item_info.get("lore") or []
        if not enchants and not lore:
            return False
        try:
            meta = item_stack.item_meta
            if meta is None:
                return False
            applied = False
            if enchants:
                for enchant_id, level in enchants.items():
                    try:
                        if hasattr(meta, "add_enchant"):
                            ok = meta.add_enchant(str(enchant_id), int(level), True)
                            applied = bool(ok) or applied
                    except TypeError:
                        # 旧 API 无 force 参数
                        try:
                            ok = meta.add_enchant(str(enchant_id), int(level))
                            applied = bool(ok) or applied
                        except Exception as e:
                            self._log(
                                "warning",
                                f"[ARCInventory] Failed to apply enchant {enchant_id}: {e}",
                            )
                    except Exception as e:
                        self._log(
                            "warning",
                            f"[ARCInventory] Failed to apply enchant {enchant_id}: {e}",
                        )
            if lore and hasattr(meta, "lore"):
                try:
                    meta.lore = list(lore)
                    applied = True
                except Exception as e:
                    self._log("warning", f"[ARCInventory] Failed to apply lore: {e}")
            if hasattr(item_stack, "set_item_meta"):
                item_stack.set_item_meta(meta)
            return applied
        except Exception as e:
            self._log("warning", f"[ARCInventory] Apply item meta: {e}")
            return False

    def _restore_item_nbt(self, item_stack: Any, nbt_b64: str) -> bool:
        """还原用户 NBT；成功返回 True。"""
        try:
            from endstone.nbt import load

            raw_nbt = base64.b64decode(nbt_b64)
            tag, _name = load(raw_nbt, byte_order="little")
            if tag is None or not hasattr(item_stack, "nbt"):
                return False
            item_stack.nbt = tag
            # 校验：写回后应仍能序列化出非空 NBT
            check = self._serialize_item_nbt(item_stack)
            return bool(check)
        except Exception as e:
            self._log("warning", f"[ARCInventory] Restore item NBT failed: {e}")
            return False

    def _enchants_from_nbt_b64(self, nbt_b64: str) -> Dict[str, int]:
        """从已序列化的 nbt_b64 解析 ench，供 NBT 直接写回失败时的 ItemMeta 回退。"""
        if not nbt_b64:
            return {}
        try:
            from endstone.inventory import ItemStack
            from endstone.nbt import load

            raw_nbt = base64.b64decode(nbt_b64)
            tag, _name = load(raw_nbt, byte_order="little")
            if tag is None:
                return {}
            probe = ItemStack(type="minecraft:enchanted_book", amount=1)
            if not hasattr(probe, "nbt"):
                return {}
            probe.nbt = tag
            return self._get_enchants_from_nbt(probe)
        except Exception:
            return {}

    def _prepare_give_stack(
        self, item_type_id: str, amount: int, item_data: int, item_info: Dict[str, Any]
    ) -> Any:
        """构造待发放的 ItemStack：遵守 max_stack，优先 NBT，失败则回退附魔/Lore。"""
        from endstone.inventory import ItemStack

        item_stack = ItemStack(type=item_type_id, amount=1, data=item_data)
        max_stack = self._resolve_max_stack(item_stack)
        give_amount = min(max(1, int(amount)), max_stack)
        item_stack.amount = give_amount

        nbt_b64 = item_info.get("nbt_b64")
        nbt_ok = False
        if nbt_b64:
            nbt_ok = self._restore_item_nbt(item_stack, nbt_b64)
            if not nbt_ok:
                self._log(
                    "warning",
                    f"[ARCInventory] NBT restore failed for {item_type_id}; fallback to enchants/lore.",
                )

        if not nbt_ok:
            fallback_info = dict(item_info)
            enchants = dict(fallback_info.get("enchants") or {})
            if not enchants and nbt_b64:
                enchants = self._enchants_from_nbt_b64(nbt_b64)
                if enchants:
                    fallback_info["enchants"] = enchants
            self._apply_item_meta_extras(item_stack, fallback_info)

        # 防止构造/还原过程改变数量
        if getattr(item_stack, "amount", give_amount) != give_amount:
            try:
                item_stack.amount = give_amount
            except Exception:
                pass
        return item_stack

    def _item_type_id(self, stack: Any) -> str:
        item_type = getattr(stack, "type", None)
        if item_type is None:
            return ""
        ident = getattr(item_type, "id", None)
        if ident:
            return str(ident)
        return str(item_type)

    def serialize_item(self, item_stack: Any) -> Optional[Dict[str, Any]]:
        """将 ItemStack 序列化为可存档的 item_info（含可选 nbt_b64 / enchants / lore）。"""
        if item_stack is None:
            return None
        try:
            if int(getattr(item_stack, "amount", 0) or 0) <= 0:
                return None
            type_id = self._item_type_id(item_stack)
            if not type_id or type_id == "minecraft:air":
                return None
            entry: Dict[str, Any] = {
                "type": type_id,
                "count": int(item_stack.amount),
                "data": int(getattr(item_stack, "data", 0) or 0),
            }
            nbt_b64 = self._serialize_item_nbt(item_stack)
            if nbt_b64:
                entry["nbt_b64"] = nbt_b64
            enchants = self._get_item_enchants(item_stack)
            if enchants:
                entry["enchants"] = enchants
            lore = self._get_item_lore(item_stack)
            if lore:
                entry["lore"] = lore
            return entry
        except Exception:
            return None

    def make_item_stack(self, item_info: Dict[str, Any]) -> Any:
        """由 item_info 构造 ItemStack（公开版 _prepare_give_stack）。"""
        type_id = str(item_info.get("type") or "")
        if not type_id or type_id == "minecraft:air":
            return None
        amount = max(1, int(item_info.get("count", 1) or 1))
        data = int(item_info.get("data", 0) or 0)
        return self._prepare_give_stack(type_id, amount, data, item_info)

    def set_slot(
        self,
        player: Any,
        slot: int,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入主背包指定槽位；item_info 为 None 则清空该格。"""
        try:
            inventory = player.inventory
            slot_i = int(slot)
            if slot_i < 0 or slot_i >= int(getattr(inventory, "size", 0) or 0):
                return False
            if not item_info:
                inventory.set_item(slot_i, None)
                return True
            stack = self.make_item_stack(item_info)
            inventory.set_item(slot_i, stack)
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] set_slot error: {e}")
            return False

    def set_armor_slot(
        self,
        player: Any,
        armor_slot: str,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入护甲/副手槽；item_info 为 None 则清空。"""
        attr = str(armor_slot or "").strip()
        if attr not in ARMOR_ATTRS:
            return False
        try:
            inventory = player.inventory
            if not hasattr(inventory, attr):
                return False
            if not item_info:
                setattr(inventory, attr, None)
                return True
            stack = self.make_item_stack(item_info)
            setattr(inventory, attr, stack)
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] set_armor_slot error: {e}")
            return False

    def _give_into_slot_range_end(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        reserved: int,
    ) -> int:
        """从背包末尾向前填充，避开前 reserved 格。"""
        inventory = player.inventory
        remaining = max(0, int(item_info.get("count", 0) or 0))
        if remaining <= 0:
            return 0
        item_type_id = str(item_info["type"])
        item_data = int(item_info.get("data", 0) or 0)
        size = int(getattr(inventory, "size", 0) or 0)
        start = max(int(reserved or 0), 0)
        given = 0
        for i in range(size - 1, start - 1, -1):
            if remaining <= 0:
                break
            try:
                existing = inventory.get_item(i)
            except Exception:
                continue
            existing_id = self._item_type_id(existing) if existing else ""
            existing_amt = int(getattr(existing, "amount", 0) or 0) if existing else 0
            if not existing or not existing_id or existing_amt <= 0:
                put_info = dict(item_info)
                put_info["count"] = remaining
                stack = self.make_item_stack(put_info)
                if stack is None:
                    break
                put = int(getattr(stack, "amount", 0) or 0)
                inventory.set_item(i, stack)
                remaining -= put
                given += put
                continue
            if existing_id != item_type_id:
                continue
            if int(getattr(existing, "data", 0) or 0) != item_data:
                continue
            max_stack = self._resolve_max_stack(existing)
            space = max_stack - existing_amt
            if space <= 0:
                continue
            put = min(space, remaining)
            existing.amount = existing_amt + put
            inventory.set_item(i, existing)
            remaining -= put
            given += put
        return int(given)

    def give_item_count(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> int:
        """
        尝试向玩家背包发放物品，返回**实际成功发放的数量**（可能为部分）。
        - slot：写入主背包指定槽（覆盖该格；受 max_stack 限制）
        - armor_slot：写入护甲/副手属性名
        - prefer_end + reserved：从末尾向前填，避开前 reserved 格；仍放不下再 add_item
        按物品 max_stack_size 分堆发放（镐等不可堆叠会逐个发放）。
        """
        try:
            total_amount = int(item_info.get("count", 0) or 0)
            if total_amount <= 0:
                self._log("warning", f"[ARCInventory] Invalid item amount: {total_amount}")
                return 0
            armor = str(armor_slot or "").strip()
            if armor:
                if armor not in ARMOR_ATTRS:
                    return 0
                put_info = dict(item_info)
                put_info["count"] = min(total_amount, 1) if total_amount > 0 else 0
                # 护甲通常 1 件；仍按请求 count 写入 amount
                put_info["count"] = total_amount
                stack = self.make_item_stack(put_info)
                if stack is None:
                    return 0
                inventory = player.inventory
                if not hasattr(inventory, armor):
                    return 0
                setattr(inventory, armor, stack)
                return int(getattr(stack, "amount", 0) or 0)

            if slot is not None:
                stack = self.make_item_stack(dict(item_info))
                if stack is None:
                    return 0
                try:
                    player.inventory.set_item(int(slot), stack)
                except Exception:
                    return 0
                return int(getattr(stack, "amount", 0) or 0)

            given_total = 0
            remaining_to_give = total_amount
            if prefer_end:
                end_info = dict(item_info)
                end_info["count"] = remaining_to_give
                placed = self._give_into_slot_range_end(
                    player, end_info, reserved=int(reserved or 0)
                )
                given_total += placed
                remaining_to_give -= placed
                if remaining_to_give <= 0:
                    return int(given_total)

            inventory = player.inventory
            item_type_id = item_info["type"]
            item_data = item_info.get("data", 0)
            while remaining_to_give > 0:
                chunk_info = dict(item_info)
                chunk_info["count"] = remaining_to_give
                item_stack = self._prepare_give_stack(
                    item_type_id, remaining_to_give, item_data, chunk_info
                )
                stack_amount = int(getattr(item_stack, "amount", 0) or 0)
                if stack_amount <= 0:
                    break
                remaining_items = inventory.add_item(item_stack)
                if remaining_items:
                    try:
                        if hasattr(remaining_items, "get"):
                            first_remaining = remaining_items.get(0)
                        elif isinstance(remaining_items, dict):
                            first_remaining = next(iter(remaining_items.values()), None)
                        else:
                            first_remaining = (
                                remaining_items[0]
                                if isinstance(remaining_items, list)
                                and len(remaining_items) > 0
                                else None
                            )
                        remaining_amount = (
                            int(getattr(first_remaining, "amount", 0) or 0)
                            if first_remaining is not None
                            else 0
                        )
                        added_amount = max(0, stack_amount - remaining_amount)
                        if added_amount > 0:
                            given_total += added_amount
                        remaining_to_give -= added_amount
                        if added_amount == 0:
                            self._log(
                                "warning",
                                f"[ARCInventory] Player {player.name} inventory full",
                            )
                            break
                    except Exception as e:
                        self._log(
                            "warning",
                            f"[ARCInventory] Error calculating remaining: {e}",
                        )
                        break
                else:
                    remaining_to_give -= stack_amount
                    given_total += stack_amount
            return int(given_total)
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Give item to player error: {str(e)}"
            )
            return 0

    def clear_inventory(
        self,
        player: Any,
        *,
        include_contents: bool = True,
        include_armor: bool = True,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> bool:
        """清空主背包（可按槽范围）与/或护甲。"""
        try:
            inventory = player.inventory
            if include_contents:
                size = int(getattr(inventory, "size", 0) or 0)
                if slot_min is None and slot_max is None and hasattr(inventory, "clear"):
                    inventory.clear()
                else:
                    for i in range(size):
                        if not self._slot_in_range(i, slot_min, slot_max):
                            continue
                        try:
                            inventory.set_item(i, None)
                        except Exception:
                            continue
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        setattr(inventory, attr, None)
                    except Exception:
                        continue
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] clear_inventory error: {e}")
            return False

    def snapshot_inventory(
        self,
        player: Any,
        *,
        include_armor: bool = True,
    ) -> Dict[str, Any]:
        """
        全量快照：含空槽（None）。
        返回 {"size": N, "slots": [...], "armor": {...}?}
        """
        out: Dict[str, Any] = {"size": 0, "slots": []}
        try:
            inventory = player.inventory
            size = int(getattr(inventory, "size", 0) or 0)
            slots: List[Optional[Dict[str, Any]]] = []
            for i in range(size):
                try:
                    slots.append(self.serialize_item(inventory.get_item(i)))
                except Exception:
                    slots.append(None)
            out["size"] = size
            out["slots"] = slots
            if include_armor:
                armor: Dict[str, Any] = {}
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        armor[attr] = self.serialize_item(getattr(inventory, attr, None))
                    except Exception:
                        armor[attr] = None
                out["armor"] = armor
            return out
        except Exception as e:
            self._log("error", f"[ARCInventory] snapshot_inventory error: {e}")
            return out

    def restore_inventory(
        self,
        player: Any,
        snapshot: Dict[str, Any],
        *,
        include_armor: bool = True,
    ) -> bool:
        """按快照还原主背包与护甲（先清空对应区域）。

        支持两种格式：
        - 扁平：{"size", "slots", "armor"?}
        - 枪战兼容：{"inventory": {"slots":...}, "armor":...}
        """
        if not isinstance(snapshot, dict):
            return False
        try:
            inventory = player.inventory
            nested = snapshot.get("inventory")
            if isinstance(nested, dict) and ("slots" in nested or "size" in nested):
                slots = nested.get("slots")
                armor = snapshot.get("armor") if "armor" in snapshot else nested.get("armor")
            else:
                slots = snapshot.get("slots")
                armor = snapshot.get("armor")

            if slots is not None:
                size = int(getattr(inventory, "size", 0) or 0)
                if hasattr(inventory, "clear"):
                    inventory.clear()
                for i, slot_data in enumerate(slots or []):
                    if i >= size:
                        break
                    try:
                        stack = self.make_item_stack(slot_data) if slot_data else None
                        inventory.set_item(i, stack)
                    except Exception:
                        continue
            if include_armor and isinstance(armor, dict):
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        data = armor.get(attr)
                        setattr(
                            inventory,
                            attr,
                            self.make_item_stack(data) if data else None,
                        )
                    except Exception:
                        try:
                            setattr(inventory, attr, None)
                        except Exception:
                            pass
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] restore_inventory error: {e}")
            return False
