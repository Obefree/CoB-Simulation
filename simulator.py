import json
import random
from collections import defaultdict, Counter
import re
import sys
import os

# --- Глобальные переменные для Flask ---
MAIN_CARDS = None
STARTER_CARDS = None

# Включаем отладку приоритетов покупки
DEBUG_BUY_STRATEGY = True

# --- Лог-файл для debug ---
DEBUG_LOG_FILE = 'debug.log'

# --- Цветовая подсветка для debug_log ---
class AnsiColor:
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

ACTIVE_LOG_OPTIONS = set()

def debug_log(msg, log_type=None, color=None):
    if log_type and log_type not in ACTIVE_LOG_OPTIONS:
        return
    if color:
        msg = f"{color}{msg}{AnsiColor.RESET}"
    print(msg)
    try:
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(str(msg)+'\n')
    except Exception as e:
        pass

try:
    # Если запускается как отдельный скрипт
    if MAIN_CARDS is None or STARTER_CARDS is None:
        with open('cards.json', encoding='utf-8') as f:
            data = json.load(f)
            MAIN_CARDS = data['main']
            STARTER_CARDS = data['starters']
except FileNotFoundError:
    # Если файла нет — не страшно, Flask сам подставит карты
    pass

# --- Классы ---
class Card:
    def __init__(self, name, effect1, effect2, cost, color, effect1text='', effect2text=''):
        self.name = name
        self.effect1 = effect1
        self.effect2 = effect2
        self.cost = cost
        self.color = color
        self.effect1text = effect1text
        self.effect2text = effect2text
        self.absorbed_damage = 0
        self.defends = False
        self.defense = 0
        self.hp = 0
        self.is_gear = False
        self.uses = 0  # для трэша Priestess
        # Gear определяется по эффектам (теперь ищем во всех effect-полях)
        for eff in [effect1, effect2, effect1text, effect2text]:
            if eff:
                m = re.search(r'\{Def_Y_Text (\d+)\}', eff)
                if m:
                    self.defends = True
                    self.defense = int(m.group(1))
                    self.absorbed_damage = 0
                    self.is_gear = True
                m2 = re.search(r'\{Def_N_Text (\d+)\}', eff)
                if m2:
                    self.defends = False
                    self.defense = int(m2.group(1))
                    self.hp = int(m2.group(1))
                    self.is_gear = True
    def __repr__(self):
        return f"{self.name} (Cost: {self.cost}, Color: {self.color}, Effects: {self.effects})"

# --- Генерация стартовой колоды ---
def create_starting_deck():
    deck = []
    for card in STARTER_CARDS:
        if card['name'].lower() == 'prayer':
            deck += [Card(card['name'], card['effect1'], card['effect2'], 0, card['color'], card.get('effect1text',''), card.get('effect2text','')) for _ in range(7)]
        elif card['name'].lower() == 'strike':
            deck += [Card(card['name'], card['effect1'], card['effect2'], 0, card['color'], card.get('effect1text',''), card.get('effect2text','')) for _ in range(3)]
    random.shuffle(deck)
    return deck

# --- Priestess ---
def get_priestess():
    for card in STARTER_CARDS:
        if card['name'].lower() == 'priestess':
            # Используем все эффекты из стартовой карты Priestess
            return Card(
                card['name'],
                card.get('effect1', ''),
                card.get('effect2', ''),
                card.get('cost', 2),
                card.get('color', ''),
                card.get('effect1text', ''),
                card.get('effect2text', '')
            )
    return None

# --- Игрок ---
class Player:
    def __init__(self, name):
        self.name = name
        self.deck = create_starting_deck()
        self.hand = []
        self.discard = []
        self.health = 50
        self.poison = 0
        self.bleed = 0
        self.gear = []
        self.trash_pile = []
        self.gear_saved_damage = 0
        self.spy_discarded = 0
        self.gear_destroyed = 0
        self.priestess_uses = {}  # {card_id: count}
        self.priestess_global_uses = 0  # глобальный счётчик применений Priestess
        # Новые поля для статистики
        self.total_damage_dealt = 0
        self.total_poison_dealt = 0
        self.total_bleed_dealt = 0
        self.total_heal_received = 0
        self.total_poison_heal_received = 0
        self.total_bleed_heal_received = 0
        random.shuffle(self.deck)
        self.draw(5)
    def draw(self, n):
        for _ in range(n):
            if not self.deck:
                self.deck = self.discard
                self.discard = []
                random.shuffle(self.deck)
            if self.deck:
                self.hand.append(self.deck.pop())
            else:
                break
    def end_turn(self):
        for card in self.hand:
            self.discard.append(card)
        self.hand = []
        self.draw(5)
        # --- После конца хода: gear absorbed_damage статистика и сброс ---
        for gear in self.gear:
            # Если нужно, можно копить статистику по absorbed_damage вот тут:
            # Например: self.total_gear_absorbed += gear.absorbed_damage
            gear.absorbed_damage = 0
    def start_turn_statuses(self):
        if self.bleed > 0:
            self.health -= self.bleed
            self.bleed = max(0, self.bleed - 1)
        if self.poison >= 20:
            return True  # проигрыш
        return False
    def apply_damage(self, dmg):
        dmg = safe_int(dmg)
        # 1. Gear с защитой игрока (defends=True)
        for gear in list(self.gear):
            if getattr(gear, 'defends', False) and gear.defense > 0:
                absorb = min(dmg, gear.defense - gear.absorbed_damage)
                gear.absorbed_damage += absorb
                self.gear_saved_damage += absorb
                dmg -= absorb
                # --- Новая логика: если gear полностью поглотил урона defense за этот ход, gear уходит в discard ---
                if gear.absorbed_damage >= gear.defense:
                    self.gear.remove(gear)
                    self.discard.append(gear)  # gear идёт в discard
                if dmg <= 0:
                    return
        # 2. Gear с собственным HP (defends=False) не защищает игрока, но может быть уничтожена отдельным эффектом
        self.health -= dmg
    def heal(self, amount):
        self.health += amount
    def heal_bleed(self, amount):
        self.bleed = max(0, self.bleed - amount)
    def heal_poison(self, amount):
        self.poison = max(0, self.poison - amount)

# --- Рынок ---
class TradeMarket:
    def __init__(self, all_cards, trade_row_size=5):
        self.trade_deck = []
        for card in all_cards:
            self.trade_deck += [Card(card['name'], card['effect1'], card['effect2'], card['cost'], card['color'], card.get('effect1text',''), card.get('effect2text','')) for _ in range(card['copies'])]
        random.shuffle(self.trade_deck)
        self.trade_row_size = trade_row_size
        self.trade_row = []
        self.refill_trade_row()
    def refill_trade_row(self):
        while len(self.trade_row) < self.trade_row_size and self.trade_deck:
            self.trade_row.append(self.trade_deck.pop())
    def buy_card(self, card_index, player):
        if 0 <= card_index < len(self.trade_row):
            card = self.trade_row.pop(card_index)
            player.discard.append(card)
            self.refill_trade_row()  # ГАРАНТИРОВАННО пополняем рынок после покупки
            return card
        return None
    def get_market_deck_summary(self):
        from collections import Counter
        return dict(Counter([c.name for c in self.trade_deck]))

# --- Стратегии ---
def buy_strategy(player, market, total_blessing, spent_blessing, pattern="default", user_strategy=None, turn_num=1, log_if=None):
    get_card_priority_func = globals().get('get_card_priority_func', None)
    if user_strategy and get_card_priority_func:
        priority_func, is_enabled = get_card_priority_func(user_strategy)
        max_cost = user_strategy.get('max_cost', 20)
        # --- Новый блок: разделяем effect_priority_zone по 'priestess' ---
        effect_priority = user_strategy.get('effect_priority', [])
        if isinstance(effect_priority, dict):
            zone_key = str(turn_num if turn_num <= 4 else 2 if turn_num <= 8 else 3)
            effect_priority_zone = effect_priority.get(zone_key, [])
            log_if('card_filter', f"[DEBUG-PRIO] zone_key: {zone_key}, effect_priority dict keys: {list(effect_priority.keys())}")
        else:
            effect_priority_zone = effect_priority
        log_if('card_filter', f"[DEBUG-PRIO] effect_priority_zone: {effect_priority_zone}")
        effect_priority_zone_lc = [e.lower() for e in effect_priority_zone]
        if 'priestess' in effect_priority_zone_lc:
            idx_priestess = effect_priority_zone_lc.index('priestess')
            effect_priority_main = [e for e in effect_priority_zone[:idx_priestess]]
            has_priestess = True
        else:
            effect_priority_main = effect_priority_zone
            has_priestess = False
        log_if('card_filter', f"[DEBUG-PRIO] effect_priority_main: {effect_priority_main}")
        # --- Вспомогательная функция для разворачивания вложенных эффектов ---
        def flatten_effects(effects):
            flat = []
            for eff in effects:
                if isinstance(eff, list):
                    flat.extend(flatten_effects(eff))
                elif isinstance(eff, tuple) and len(eff) > 0:
                    flat.append(eff[0])
                elif isinstance(eff, str):
                    flat.append(eff)
            return flat
        # --- Фильтруем карты только по эффектам до priestess ---
        def card_has_priority_effect(card):
            effects = []
            for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                val = getattr(card, field, None)
                if val:
                    effects += parse_effects_from_string(val)
            prio_effects = [e.strip().lower() for e in effect_priority_main]
            # --- Спец. обработка gear ---
            if 'gear' in prio_effects and getattr(card, 'is_gear', False):
                log_if('card_filter', f"[DEBUG-CHECK] {card.name}: is_gear=True, ищем в={prio_effects}, результат=True")
                return True
            effect_names = [e.strip().lower() for e in flatten_effects(effects)]
            result = any(eff in prio_effects for eff in effect_names)
            log_if('card_filter', f"[DEBUG-CHECK] {card.name}: эффекты={effect_names}, ищем в={prio_effects}, результат={result}")
            for eff in effects:
                log_if('card_filter', f"[DEBUG-CHECK-RAW] {card.name}: raw_eff={eff}")
            return result
        # --- DEBUG: Выводим все карты рынка с индексом, эффектами и результатом фильтрации ---
        for idx, card in enumerate(market.trade_row):
            effects = []
            for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                val = getattr(card, field, None)
                if val:
                    effects += parse_effects_from_string(val)
            # Преобразуем эффекты в строки (только имена)
            effect_names = []
            for eff in effects:
                if isinstance(eff, (list, tuple)) and len(eff) > 0:
                    eff_name = eff[0]
                else:
                    eff_name = eff
                if isinstance(eff_name, str):
                    effect_names.append(eff_name.strip().lower())
            prio_effects = [e.strip().lower() for e in effect_priority_main]
            # Проверяем, есть ли совпадение
            has_prio = any(eff in prio_effects for eff in effect_names)
            log_if('card_filter', f"[DEBUG] [{idx}] {card.name}: эффекты={effect_names}, ищем в={prio_effects}, подходит={has_prio}")
        # --- Конец подробного лога ---
        filtered = [(i, c) for i, c in enumerate(market.trade_row) if c.cost <= (total_blessing - spent_blessing) and c.cost <= max_cost and getattr(c, 'name', None) and is_enabled(c) and card_has_priority_effect(c)]
        # --- DEBUG: Выводим все доступные для покупки карты ---
        if log_if:
            log_if('card_filter', f"[DEBUG] filtered (после фильтрации): {[c.name for i, c in filtered]}")
            log_if('card_filter', f"[DEBUG] effect_priority_zone: {user_strategy.get('effect_priority', [])}")
        if not filtered:
            # Если нет подходящих карт, и есть 'priestess' в приоритете — покупаем Priestess, если хватает денег
            if has_priestess:
                priestess = get_priestess()
                if priestess and priestess.cost <= (total_blessing - spent_blessing):
                    return 'priestess'
            return None
        # --- Сортируем по индексу эффекта (чем меньше, тем выше приоритет) ---
        def effect_score(card):
            effects = []
            for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                val = getattr(card, field, None)
                if val:
                    effects += parse_effects_from_string(val)
            def all_case_variants(e):
                return [e, e.lower(), e.upper(), e.capitalize()]
            effect_priority_variants = []
            for e in effect_priority_main:
                effect_priority_variants.extend(all_case_variants(e))
            min_idx = 1000
            min_eff = None
            for eff in effects:
                if not eff:
                    continue
                if isinstance(eff, (list, tuple)) and len(eff) > 0:
                    eff_name = eff[0]
                else:
                    eff_name = eff
                for idx, prio in enumerate(effect_priority_variants):
                    if eff_name == prio:
                        if idx < min_idx:
                            min_idx = idx
                            min_eff = eff_name
                        break
            return min_idx
        filtered.sort(key=lambda x: effect_score(x[1]))
        priestess_buy_if_2 = user_strategy.get('priestess_buy_if_2')
        if priestess_buy_if_2:
            for i, c in filtered:
                if c.name.lower() == 'priestess' and c.cost == 2:
                    if all(x[1].cost > 2 or x[1].name.lower() == 'priestess' for x in filtered):
                        return i
        return filtered[0][0]
    affordable = [(i, c) for i, c in enumerate(market.trade_row) if c.cost <= (total_blessing - spent_blessing)]
    if pattern in ["red", "blue", "green", "white"]:
        for i, card in affordable:
            if card.color == pattern:
                return i
    if pattern == "poison":
        for i, card in affordable:
            if card.color == "green":
                return i
        return affordable[0][0] if affordable else None
    if pattern == "random":
        return random.choice(affordable)[0] if affordable else None
    return affordable[0][0] if affordable else None

# --- Вспомогательная функция для парсинга эффектов из строки ---
def parse_effects_from_string(s):
    if not s:
        return []
    # OR/TO-структуры: разбиваем на альтернативы (только OR и TO в верхнем регистре)
    for sep in ['OR', 'TO']:
        if sep in s:
            parts = [p.strip() for p in s.split(sep)]
            return [parse_effects_from_string(part) for part in parts]
    # Ищет {EffectName N}, {EffectName X}, {EffectName}
    # Chain только с цветом: w_chain, b_chain, r_chain, g_chain
    result = []
    for m in re.finditer(r'\{([A-Za-z_]+)(?:\s*([\w-]+))?\}', s):
        name = m.group(1).lower()
        value = m.group(2) if m.group(2) else None
        # Только цветные Chain
        if name.endswith('_chain') and name not in ['w_chain', 'b_chain', 'r_chain', 'g_chain']:
            continue
        # Если значение X, подставляем 0
        if value == 'X':
            value = 0
        result.append((name, value))
    return result

# --- Вспомогательная функция для безопасного преобразования value к int ---
def safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0

# --- Эффекты ---
def apply_card_effects(card, player, opponent, log, trash_list):
    # --- Получаем effect_priority для зоны ---
    strat = getattr(player, 'user_strategy', None)
    turn_num = getattr(player, 'current_turn', 1)
    if strat:
        if turn_num <= 4:
            zone = 1
        elif turn_num <= 8:
            zone = 2
        else:
            zone = 3
        effect_priority = strat.get('effect_priority', [])
        if isinstance(effect_priority, dict):
            effect_priority_zone = effect_priority.get(str(zone), [])
        else:
            effect_priority_zone = effect_priority
        effect_priority_zone = [e.lower() for e in effect_priority_zone]
    else:
        effect_priority_zone = []
    # --- Применяем ВСЕ эффекты из всех effect-полей ---
    for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
        val = getattr(card, field, None)
        if val:
            effs = parse_effects_from_string(val)
            # TO-структура: применить обе части по порядку
            if effs and isinstance(effs, list) and len(effs) == 2 and all(isinstance(e, list) for e in effs):
                # TO: сначала первая часть, потом вторая
                for eff in effs[0]:
                    if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                        continue
                    name, value = eff
                    if name == 'to':
                        continue
                    # --- Немедленный трэш для trash_this ---
                    if name == 'trash_this':
                        for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                            if card in zone:
                                zone.remove(card)
                                card.trashed_by = 'trash_this'
                                player.trash_pile.append(card)
                                break
                        continue
                    if name in ['w_chain', 'b_chain', 'r_chain', 'g_chain']:
                        color = name[0]
                        color_map = {'w': 'white', 'b': 'blue', 'r': 'red', 'g': 'green'}
                        color_name = color_map.get(color, None)
                        if color_name:
                            count = sum(1 for c in player.hand+getattr(player, 'played_this_turn', []) if getattr(c, 'color', '').lower() == color_name)
                            if count < 2:
                                continue
                    apply_effect(name, value, card, player, opponent, log, trash_list)
                for eff in effs[1]:
                    if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                        continue
                    name, value = eff
                    if name == 'to':
                        continue
                    # --- Немедленный трэш для trash_this ---
                    if name == 'trash_this':
                        for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                            if card in zone:
                                zone.remove(card)
                                card.trashed_by = 'trash_this'
                                player.trash_pile.append(card)
                                break
                        continue
                    if name in ['w_chain', 'b_chain', 'r_chain', 'g_chain']:
                        color = name[0]
                        color_map = {'w': 'white', 'b': 'blue', 'r': 'red', 'g': 'green'}
                        color_name = color_map.get(color, None)
                        if color_name:
                            count = sum(1 for c in player.hand+getattr(player, 'played_this_turn', []) if getattr(c, 'color', '').lower() == color_name)
                            if count < 2:
                                continue
                    apply_effect(name, value, card, player, opponent, log, trash_list)
                continue
            # OR-структура: выбрать альтернативу по приоритету
            if effs and isinstance(effs[0], list):
                # Выбор альтернативы по приоритету
                best_idx = 1000
                best_alt = effs[0]
                found = False
                for alt in effs:
                    for eff in alt:
                        if eff[0] in effect_priority_zone:
                            idx = effect_priority_zone.index(eff[0])
                            if idx < best_idx:
                                best_idx = idx
                                best_alt = alt
                                found = True
                # Если ни один не найден по приоритету — берём первый вариант
                chosen = best_alt if found else effs[0]
                for eff in chosen:
                    if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                        continue
                    name, value = eff
                    if name == 'to':
                        continue
                    # --- Немедленный трэш для trash_this ---
                    if name == 'trash_this':
                        for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                            if card in zone:
                                zone.remove(card)
                                card.trashed_by = 'trash_this'
                                player.trash_pile.append(card)
                                break
                        continue
                    if name in ['w_chain', 'b_chain', 'r_chain', 'g_chain']:
                        color = name[0]  # w, b, r, g
                        color_map = {'w': 'white', 'b': 'blue', 'r': 'red', 'g': 'green'}
                        color_name = color_map.get(color, None)
                        if color_name:
                            count = sum(1 for c in player.hand+getattr(player, 'played_this_turn', []) if getattr(c, 'color', '').lower() == color_name)
                            if count < 2:
                                continue
                    apply_effect(name, value, card, player, opponent, log, trash_list)
            else:
                for eff in effs:
                    if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                        continue
                    name, value = eff
                    if name == 'to':
                        continue
                    # --- Немедленный трэш для trash_this ---
                    if name == 'trash_this':
                        for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                            if card in zone:
                                zone.remove(card)
                                card.trashed_by = 'trash_this'
                                player.trash_pile.append(card)
                                break
                        continue
                    if name in ['w_chain', 'b_chain', 'r_chain', 'g_chain']:
                        color = name[0]
                        color_map = {'w': 'white', 'b': 'blue', 'r': 'red', 'g': 'green'}
                        color_name = color_map.get(color, None)
                        if color_name:
                            count = sum(1 for c in player.hand+getattr(player, 'played_this_turn', []) if getattr(c, 'color', '').lower() == color_name)
                            if count < 2:
                                continue
                    apply_effect(name, value, card, player, opponent, log, trash_list)
    # Priestess trash logic (имитация {Trash_this})
    if card.name.lower() == 'priestess':
        strat = getattr(player, 'user_strategy', None)
        trash_after = None
        if strat:
            trash_after = strat.get('priestess_trash_after')
        if trash_after is None or trash_after == 0:
            trash_after = 1  # По умолчанию трэшить каждую сыгранную Priestess
        player.priestess_global_uses = getattr(player, 'priestess_global_uses', 0) + 1
        if player.priestess_global_uses % trash_after == 0:
            # ВРЕМЕННОЕ ЛОГИРОВАНИЕ для диагностики
            # print(f"DEBUG: Трэшим Priestess id={id(card)}")
            # for zname, zone in [('hand', player.hand), ('played_this_turn', getattr(player, 'played_this_turn', [])), ('discard', player.discard), ('deck', player.deck), ('gear', player.gear)]:
            #     for c in zone:
            #         print(f"  {zname}: {c.name} id={id(c)}")
            # Трэшим текущую Priestess из всех зон (аналогично trash_this)
            for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                if card in zone:
                    zone.remove(card)
                    card.trashed_by = 'trash_this'
                    player.trash_pile.append(card)
                    # print(f"DEBUG: Priestess id={id(card)} затрешена из {zone}")
                    break

def apply_effect(name, value, card, player, opponent, log, trash_list):
    value = safe_int(value)
    if name == 'damage':
        opponent.apply_damage(value)
        player.total_damage_dealt += value
        if log:
            debug_log(f"  {card.name} наносит {value} урона!", log_type="effects")
    elif name == 'heal':
        player.heal(value)
        player.total_heal_received += value
        if log:
            debug_log(f"  {card.name} лечит на {value}!", log_type="effects")
    elif name == 'heal_bleed':
        player.heal_bleed(value)
        player.total_bleed_heal_received += value
        if log:
            debug_log(f"  {card.name} снимает bleed на {value}!", log_type="effects")
    elif name == 'heal_poison':
        player.heal_poison(value)
        player.total_poison_heal_received += value
        if log:
            debug_log(f"  {card.name} снимает poison на {value}!", log_type="effects")
    elif name == 'bleed':
        opponent.bleed += value
        player.total_bleed_dealt += value
        if log:
            debug_log(f"  {card.name} даёт {value} bleed!", log_type="effects")
    elif name == 'poison':
        opponent.poison += value
        player.total_poison_dealt += value
        if log:
            debug_log(f"  {card.name} даёт {value} яда!", log_type="effects")
    elif name == 'trash':
        def is_starter(c):
            return c.name.lower() in ['strike', 'prayer']
        # Сброс
        discard_starters = [c for c in player.discard if is_starter(c)]
        if discard_starters:
            c = discard_starters[0]
            player.discard.remove(c)
            c.trashed_by = 'trash'
            player.trash_pile.append(c)
            return None  # из сброса — не влияет на hand_queue
        # Рука
        hand_starters = [c for c in player.hand if is_starter(c)]
        if hand_starters:
            c = hand_starters[0]
            player.hand.remove(c)
            c.trashed_by = 'trash'
            player.trash_pile.append(c)  # Теперь карта из руки тоже попадает в трэш
            return c  # вернуть удалённую карту из руки
        return None
    elif name == 'draw':
        player.draw(value)
        if log:
            debug_log(f"  {card.name} добирает {value} карт(ы)!", log_type="effects")
    elif name == 'stun':
        to_discard = sorted(opponent.hand, key=lambda c: c.cost)[:value]
        for c in to_discard:
            opponent.hand.remove(c)
            opponent.discard.append(c)
            if log:
                debug_log(f"  [Stun] {opponent.name} сбрасывает карту: {c.name}", log_type="effects")
    elif name == 'spy':
        # Смотрим верхние value карт колоды оппонента
        to_check = []
        for _ in range(value):
            if opponent.deck:
                to_check.append(opponent.deck.pop())
        # Не базовые — в сброс, базовые — обратно на колоду
        starter_names = {'prayer', 'strike'}
        to_return = []
        for c in to_check:
            if c.name.lower() in starter_names:
                to_return.append(c)
            else:
                opponent.discard.append(c)
                opponent.spy_discarded += 1
                if log:
                    debug_log(f"  [Spy] {c.name} сброшена!", log_type="effects")
        # Базовые возвращаем на колоду (в том же порядке)
        opponent.deck.extend(reversed(to_return))
        if log:
            debug_log(f"  [Spy] Базовые карты возвращены на колоду: {[c.name for c in to_return]}", log_type="effects")
    elif name == 'steal':
        for _ in range(value):
            if opponent.deck:
                stolen = opponent.deck.pop()
                player.discard.append(stolen)
                if log:
                    debug_log(f"  [Steal] Украдена карта: {stolen.name}", log_type="effects")
    elif name == 'destroy':
        # Уничтожить первую gear-карту оппонента
        destroyed = None
        for g in opponent.gear:
            destroyed = g
            break
        if destroyed:
            opponent.gear.remove(destroyed)
            opponent.gear_destroyed += 1
            if log:
                debug_log(f"  [Destroy] Уничтожена gear-карта: {destroyed.name}", log_type="effects")

# --- Симуляция одной партии ---
def simulate_game(pattern1, pattern2, log=False, max_turns=30, first_player=0, custom_hp=None, collect_log=False, user_strategy1=None, user_strategy2=None, print_market_deck=False, log_options=None):
    if log_options is None:
        log_options = ['effects', 'hand', 'deck', 'discard', 'trash', 'hp', 'poison', 'bleed', 'buys', 'all_effects', 'debug', 'card_filter']
    def log_if(option, msg, color=None):
        if log_options is None or option in log_options:
            debug_log(msg, log_type=option, color=color)
    player1 = Player("P1")
    player2 = Player("P2")
    market = TradeMarket(MAIN_CARDS)
    player1.market = market
    player2.market = market
    priestess = get_priestess()
    players = [player1, player2]
    patterns = [pattern1, pattern2]
    if print_market_deck:
        debug_log(f"[MARKET] Состав колоды рынка в начале игры: {market.get_market_deck_summary()}", log_type="market")
    if custom_hp is not None:
        for p in [player1, player2]:
            if p.name == "P1":
                p.health = custom_hp[0]
            else:
                p.health = custom_hp[1]
    hp_history = []
    poison_history = []
    winner = None
    detailed_log = [] if collect_log else None
    for turn in range(max_turns):
        turn_log = [] if collect_log else None
        if log:
            debug_log(f"\n=== Ход {turn+1} ===", log_type="debug", color=AnsiColor.GREEN)

        for idx, player in enumerate(players):
            opponent = players[1 - idx]
            color = AnsiColor.GREEN if idx == 0 else AnsiColor.BLUE
            debug_log(f"\n=== Ход {turn+1} (P{idx+1}) ===", log_type="debug", color=color)
            # --- Проверка на проигрыш до начала хода ---
            if player.health <= 0 or player.poison >= 20:
                winner = opponent.name
                if log:
                    debug_log(f"Игрок {idx+1} проиграл (до начала хода)!", log_type="player", color=color)
                if collect_log:
                    if turn_log is not None:
                        turn_log.append({'player': player.name, 'lost': True, 'reason': 'start_turn'})
                break
            lost = player.start_turn_statuses()
            if log:
                debug_log(f"Игрок {idx+1}: HP={player.health}, Poison={player.poison}, Bleed={player.bleed}, Hand={[c.name for c in player.hand]}", log_type="player", color=color)
            if collect_log:
                turn_log.append({
                    'player': player.name,
                    'start_status': {
                        'hp': player.health,
                        'poison': player.poison,
                        'bleed': player.bleed,
                        'hand': [c.name for c in player.hand],
                        'deck': [c.name for c in player.deck],
                        'discard': [c.name for c in player.discard],
                        'gear': [c.name for c in player.gear],
                    }
                })
            if lost:
                winner = opponent.name
                if log:
                    debug_log(f"Игрок {idx+1} проиграл!", log_type="player", color=color)
                if collect_log:
                    turn_log.append({'player': player.name, 'lost': True, 'reason': 'start_turn_statuses'})
                break
            # --- Новый подсчёт Blessing по всей руке ---
            total_blessing = 0
            for card in player.hand:
                for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                    val = getattr(card, field, None)
                    if val:
                        for eff in parse_effects_from_string(val):
                            if isinstance(eff, (list, tuple)) and len(eff) == 2 and eff[0] == 'blessing':
                                try:
                                    blessing_val = int(eff[1]) if eff[1] is not None else 0
                                except (TypeError, ValueError):
                                    blessing_val = 0
                                total_blessing += blessing_val
            if collect_log:
                # Добавляем информацию о blessing в последний элемент turn_log
                if turn_log and len(turn_log) > 0:
                    turn_log[-1]['total_blessing'] = total_blessing
            trash_list = []
            played = set()
            hand_queue = list(player.hand)
            played_this_turn = []
            played_ids = set()
            effects_this_turn = []
            # --- DEBUG: выводим стартовое состояние ---
            log_if('debug', f"[DEBUG] {player.name} старт хода: hand={[c.name for c in player.hand]}, deck={[c.name for c in player.deck]}, discard={[c.name for c in player.discard]}", color)
            log_if('player', f"[PLAYER] {player.name} (начало): HP={player.health}, Poison={player.poison}, Bleed={player.bleed}", color)
            log_if('hand', f"Hand({len(player.hand)}): {[c.name for c in player.hand]}", color)
            log_if('deck', f"Deck({len(player.deck)}): {[c.name for c in player.deck]}", color)
            log_if('discard', f"Discard({len(player.discard)}): {[c.name for c in player.discard]}", color)
            log_if('gear', f"Gear({len(player.gear)}): {[c.name for c in player.gear]}", color)
            log_if('trash', f"Trash({len(player.trash_pile)}): {[c.name for c in player.trash_pile]}", color)
            log_if('hp', f"HP: {player.health}", color)
            log_if('poison', f"Poison: {player.poison}", color)
            log_if('bleed', f"Bleed: {player.bleed}", color)
            debug_log(f"         Hand({len(player.hand)}): {[c.name for c in player.hand]}", log_type="debug", color=color)
            debug_log(f"         Deck({len(player.deck)}): {[c.name for c in player.deck]}", log_type="debug", color=color)
            debug_log(f"         Discard({len(player.discard)}): {[c.name for c in player.discard]}", log_type="debug", color=color)
            debug_log(f"         Gear({len(player.gear)}): {[c.name for c in player.gear]}", log_type="debug", color=color)
            debug_log(f"         Trash({len(player.trash_pile)}): {[c.name for c in player.trash_pile]}", log_type="debug", color=color)
            max_play_iterations = 30
            play_iterations = 0
            while hand_queue:
                play_iterations += 1
                if play_iterations > max_play_iterations:
                    debug_log(f"[DEBUG] Превышен лимит разыгрывания карт за ход! hand_queue={[c.name for c in hand_queue]}, played_this_turn={[c.name for c in played_this_turn]}", log_type="debug", color=color)
                    break
                card = hand_queue.pop(0)
                # --- Проверка: карта должна быть в руке и не разыграна ранее (по id!) ---
                if card not in player.hand:
                    debug_log(f"[ERROR] Карта {card.name} не в руке, но пытается разыграться! Пропуск.", log_type="error", color=color)
                    continue
                if id(card) in played_ids:
                    debug_log(f"[ERROR] Карта {card.name} (id={id(card)}) уже разыграна в этом ходу! Пропуск.", log_type="error", color=color)
                    continue
                # --- DEBUG: какие эффекты будут применяться ---
                effs_to_apply = []
                for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                    val = getattr(card, field, None)
                    if val:
                        parsed = parse_effects_from_string(val)
                        debug_log(f"[DEBUG] Разыгрывается {card.name}: {field}='{val}' -> {parsed}", log_type="debug", color=color)
                        effs_to_apply += parsed
                debug_log(f"[DEBUG] Разыгрывается карта {card.name}, эффекты к применению: {effs_to_apply}", log_type="debug", color=color)
                if not effs_to_apply:
                    debug_log(f"[DEBUG] У карты {card.name} нет применимых эффектов!", log_type="debug", color=color)
                # --- GEAR: если карта gear, кладём на стол, эффекты применяем, но не уходит в discard ---
                if getattr(card, 'is_gear', False):
                    if not any(g is card for g in player.gear):
                        player.gear.append(card)
                    for eff in parse_effects_from_string(card.effect1):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name not in ['def_y_text', 'def_n_text']:
                            apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                            if player.health <= 0 or player.poison >= 20:
                                winner = opponent.name
                                if log:
                                    debug_log(f"Игрок {idx+1} проиграл (после эффекта gear)!", log_type="player", color=color)
                                break
                    for eff in parse_effects_from_string(card.effect2):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name not in ['def_y_text', 'def_n_text']:
                            apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                            if player.health <= 0 or player.poison >= 20:
                                winner = opponent.name
                                if log:
                                    debug_log(f"Игрок {idx+1} проиграл (после эффекта gear)!", log_type="player", color=color)
                                break
                    played_this_turn.append(card)
                    played_ids.add(id(card))
                    # Удаляем gear из руки
                    if card in player.hand:
                        player.hand.remove(card)
                    if winner:
                        break
                    continue
                # --- Обычная карта ---
                def draw_hook(n):
                    n = safe_int(n)
                    before = set(id(c) for c in player.hand)
                    player.draw(n)
                    # Добавлять только реально новые карты
                    new_cards = [c for c in player.hand if id(c) not in before and c not in played_this_turn and c not in hand_queue]
                    hand_queue.extend(new_cards)
                    if log and new_cards:
                        debug_log(f"  Добрано карт: {[c.name for c in new_cards]}", log_type="effects", color=color)
                def apply_card_effects_with_draw(card, player, opponent, log, trash_list):
                    removed_from_hand = []
                    for eff in parse_effects_from_string(card.effect1):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name == 'draw':
                            draw_hook(value)
                        else:
                            res = apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                            if name == 'trash' and res is not None:
                                removed_from_hand.append(res)
                            if player.health <= 0 or player.poison >= 20:
                                return removed_from_hand, True
                    for eff in parse_effects_from_string(card.effect2):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name == 'draw':
                            draw_hook(value)
                        else:
                            res = apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                            if name == 'trash' and res is not None:
                                removed_from_hand.append(res)
                            if player.health <= 0 or player.poison >= 20:
                                return removed_from_hand, True
                    # --- УПРОЩЁННО: если это Priestess, всегда уходит в трэш после применения эффектов ---
                    if getattr(card, 'name', '').lower() == 'priestess':
                        # Проверяем, есть ли у карты эффект trash_this
                        has_trash_this = False
                        for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
                            val = getattr(card, field, None)
                            if val and '{trash_this' in val.lower():
                                has_trash_this = True
                                break
                        for zone in [player.hand, getattr(player, 'played_this_turn', []), player.discard, player.deck, player.gear]:
                            if card in zone:
                                zone.remove(card)
                                card.trashed_by = 'trash_this' if has_trash_this else 'priestess_trash'
                                player.trash_pile.append(card)
                                break
                    return removed_from_hand, False
                removed, lost_now = apply_card_effects_with_draw(card, player, opponent, log, trash_list)
                for rem_card in removed:
                    hand_queue = [c for c in hand_queue if c != rem_card]
                played_this_turn.append(card)
                played_ids.add(id(card))
                # Удаляем карту из руки и кладём в discard ТОЛЬКО если она ещё не была затрешена
                if card in player.hand:
                    player.hand.remove(card)
                    # Если карта уже в трэше, не кладём в discard
                    if card not in player.trash_pile:
                        player.discard.append(card)
                if lost_now:
                    winner = opponent.name
                    if log:
                        debug_log(f"Игрок {idx+1} проиграл (после эффекта)!", log_type="player", color=color)
                    break
            # --- Диагностика: карта разыграна более одного раза ---
            card_ids = [id(c) for c in played_this_turn]
            if len(card_ids) != len(set(card_ids)):
                debug_log(f"[ERROR] Одна и та же карта разыграна более одного раза за ход! {[c.name for c in played_this_turn]}", log_type="error", color=color)
            if winner:
                if collect_log:
                    turn_log.append({'player': player.name, 'lost': True, 'reason': 'effect'})
                break
            # Trash_this: удаляем карты из руки
            for card in trash_list:
                found = False
                zone_name = None
                for zone, zname in zip([player.hand, player.discard, player.deck, played_this_turn, player.gear], ['hand', 'discard', 'deck', 'played_this_turn', 'gear']):
                    if card in zone:
                        zone.remove(card)
                        card.trashed_by = 'trash_this'
                        player.trash_pile.append(card)
                        found = True
                        zone_name = zname
                        break
                if collect_log:
                    if not hasattr(card, 'name'):
                        cname = str(card)
                    else:
                        cname = card.name
                    if found:
                        detailed_log[-1]['actions'][-1].setdefault('trash_log', []).append(
                            {'card': cname, 'result': 'trashed', 'zone': zone_name})
                    else:
                        detailed_log[-1]['actions'][-1].setdefault('trash_log', []).append(
                            {'card': cname, 'result': 'not found', 'zone': None})
            # --- Покупка карт ---
            spent_blessing = 0
            bought_cards = []
            market_state = []
            buy_steps = [] if collect_log else None
            buy_iterations = 0
            max_buy_iterations = 10
            while True:
                buy_iterations += 1
                if buy_iterations > max_buy_iterations:
                    debug_log(f"[DEBUG] Превышен лимит покупок за ход! bought_cards={bought_cards}", log_type="debug", color=AnsiColor.YELLOW)
                    break
                if log or True:
                    debug_log(f"  Blessing на ход: {total_blessing}, потрачено: {spent_blessing}", log_type="buys", color=AnsiColor.YELLOW)
                    debug_log(f"  Доступные для покупки: {[c.name for c in market.trade_row if c.cost <= (total_blessing - spent_blessing)]}", log_type="buys", color=AnsiColor.YELLOW)
                    debug_log(f"[DEBUG] market.trade_row после refill: {[f'{c.name}({c.cost})' for c in market.trade_row]}", log_type="debug", color=AnsiColor.YELLOW)
                i = buy_strategy(player, market, total_blessing, spent_blessing, pattern=patterns[idx], user_strategy=[user_strategy1, user_strategy2][idx], turn_num=turn+1, log_if=log_if)
                if i == 'priestess':
                    # Явная покупка Priestess по стратегии или если только она разрешена
                    if priestess and priestess.cost <= (total_blessing - spent_blessing):
                        if collect_log:
                            buy_steps.append({'before': total_blessing - spent_blessing, 'card': 'Priestess', 'cost': priestess.cost, 'after': total_blessing - spent_blessing - priestess.cost})
                        player.discard.append(Card(priestess.name, priestess.effect1, priestess.effect2, priestess.cost, priestess.color, priestess.effect1text, priestess.effect2text))
                        spent_blessing += priestess.cost
                        bought_cards.append('Priestess')
                        if log or True:
                            debug_log(f"  Куплена карта: Priestess", log_type="buys", color=AnsiColor.YELLOW)
                        if collect_log:
                            market_cards = [];
                            name_count = {};
                            for c in market.trade_row:
                                key = c.name
                                name_count[key] = name_count.get(key, 0) + 1
                            for c in market.trade_row:
                                if name_count[c.name] > 0:
                                    market_cards.append({
                                        'name': c.name,
                                        'color': getattr(c, 'color', ''),
                                        'cost': getattr(c, 'cost', 0),
                                        'is_gear': getattr(c, 'is_gear', False),
                                        'copies': name_count[c.name]
                                    })
                                    name_count[c.name] = 0
                            market_state.append({'market': market_cards, 'buy': 'Priestess'})
                        # После покупки Priestess пробуем купить ещё, если хватает blessing
                        continue
                    else:
                        break
                if i is None:
                    # Если нельзя купить ничего, но можно купить Priestess — покупаем её, пока хватает blessing
                    max_priestess = 0
                    if priestess and priestess.cost > 0:
                        max_priestess = (total_blessing - spent_blessing) // priestess.cost
                    if max_priestess > 0:
                        for _ in range(max_priestess):
                            if collect_log:
                                buy_steps.append({'before': total_blessing - spent_blessing, 'card': 'Priestess', 'cost': priestess.cost, 'after': total_blessing - spent_blessing - priestess.cost})
                            player.discard.append(Card(priestess.name, priestess.effect1, priestess.effect2, priestess.cost, priestess.color, priestess.effect1text, priestess.effect2text))
                            spent_blessing += priestess.cost
                            bought_cards.append('Priestess')
                            if log or True:
                                debug_log(f"  Куплена карта: Priestess (bulk)", log_type="buys", color=AnsiColor.YELLOW)
                            if collect_log:
                                market_cards = [];
                                name_count = {};
                                for c in market.trade_row:
                                    key = c.name
                                    name_count[key] = name_count.get(key, 0) + 1
                                for c in market.trade_row:
                                    if name_count[c.name] > 0:
                                        market_cards.append({
                                            'name': c.name,
                                            'color': getattr(c, 'color', ''),
                                            'cost': getattr(c, 'cost', 0),
                                            'is_gear': getattr(c, 'is_gear', False),
                                            'copies': name_count[c.name]
                                        })
                                        name_count[c.name] = 0
                                market_state.append({'market': market_cards, 'buy': 'Priestess'})
                    break
                card = market.trade_row[i]
                if collect_log:
                    buy_steps.append({'before': total_blessing - spent_blessing, 'card': card.name, 'cost': card.cost, 'after': total_blessing - spent_blessing - card.cost})
                spent_blessing += card.cost
                market.buy_card(i, player)
                bought_cards.append(card.name)
                if log or True:
                    debug_log(f"  Куплена карта: {card.name}", log_type="buys", color=AnsiColor.YELLOW)
                if collect_log:
                    # Считаем количество одинаковых карт
                    market_cards = []
                    name_count = {}
                    for c in market.trade_row:
                        key = c.name
                        name_count[key] = name_count.get(key, 0) + 1
                    for c in market.trade_row:
                        if name_count[c.name] > 0:
                            market_cards.append({
                                'name': c.name,
                                'color': getattr(c, 'color', ''),
                                'cost': getattr(c, 'cost', 0),
                                'is_gear': getattr(c, 'is_gear', False),
                                'copies': name_count[c.name]
                            })
                            name_count[c.name] = 0  # чтобы не дублировать
                    market_state.append({'market': market_cards, 'buy': card.name})
            if log:
                debug_log(f"  Суммарно потрачено Blessing: {spent_blessing} из {total_blessing}", color=color)
            player.end_turn()
            # --- Проверка на проигрыш после конца хода ---
            if player.health <= 0 or player.poison >= 20:
                winner = opponent.name
                if log:
                    debug_log(f"Игрок {idx+1} проиграл (после конца хода)!", log_type="player", color=color)
                if collect_log:
                    turn_log.append({'player': player.name, 'lost': True, 'reason': 'end_turn'})
                break
            # --- Новое условие: проигрыш, если нет ни одной карты ---
            if not player.hand and not player.deck and not player.discard:
                winner = opponent.name
                if log:
                    debug_log(f"{player.name} проиграл: у него не осталось карт!", log_type="player", color=color)
                if collect_log:
                    turn_log.append({'player': player.name, 'lost': True, 'reason': 'no_cards'})
                break
            if collect_log:
                turn_log.append({
                    'player': player.name,
                    'end_status': {
                        'hp': player.health,
                        'poison': player.poison,
                        'bleed': player.bleed,
                        'hand': [c.name for c in player.hand],
                        'deck': [c.name for c in player.deck],
                        'discard': [c.name for c in player.discard],
                        'gear': [c.name for c in player.gear],
                    },
                    'played': played_this_turn,
                    'effects': effects_this_turn,
                    'bought': bought_cards,
                    'market_states': market_state,
                    'buy_steps': buy_steps if collect_log else None,
                    'total_blessing': total_blessing,
                    'spent_blessing': spent_blessing
                })
        if collect_log:
            detailed_log.append({'turn': turn+1, 'actions': turn_log})
        hp_history.append({'p1': player1.health, 'p2': player2.health})
        poison_history.append({'p1': player1.poison, 'p2': player2.poison})
        if winner:
            break
    # После завершения партии считаем трэш
    trash1 = sum(1 for c in player1.trash_pile if getattr(c, 'trashed_by', None) == 'trash')
    trash2 = sum(1 for c in player2.trash_pile if getattr(c, 'trashed_by', None) == 'trash')
    trash_this1 = sum(1 for c in player1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this')
    trash_this2 = sum(1 for c in player2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this')
    # Gear-статистика
    def gear_stats(player):
        # gear разыграно (по имени, уникальные экземпляры)
        played = Counter([c.name for c in player.deck + player.discard + player.hand + player.gear if getattr(c, 'is_gear', False)])
        # gear уничтожено destroy
        destroyed = Counter([c.name for c in player.trash_pile if getattr(c, 'is_gear', False) and getattr(c, 'trashed_by', None) == 'destroy'])
        # gear затрешено trash_this
        trashed = Counter([c.name for c in player.trash_pile if getattr(c, 'is_gear', False) and getattr(c, 'trashed_by', None) == 'trash_this'])
        # gear осталось на столе
        on_table = Counter([c.name for c in player.gear if getattr(c, 'is_gear', False)])
        return {
            'played': dict(played),
            'destroyed': dict(destroyed),
            'trashed': dict(trashed),
            'on_table': dict(on_table)
        }
    gear_stats1 = gear_stats(player1)
    gear_stats2 = gear_stats(player2)
    return {
        'hp_history': hp_history,
        'poison_history': poison_history,
        'winner': winner,
        'turns': turn+1,
        'player1': player1,
        'player2': player2,
        'gear_saved_damage_p1': player1.gear_saved_damage,
        'gear_saved_damage_p2': player2.gear_saved_damage,
        'spy_discarded_p1': player1.spy_discarded,
        'spy_discarded_p2': player2.spy_discarded,
        'gear_destroyed_p1': player1.gear_destroyed,
        'gear_destroyed_p2': player2.gear_destroyed,
        # Новые метрики:
        'damage_dealt1': player1.total_damage_dealt,
        'damage_dealt2': player2.total_damage_dealt,
        'poison_dealt1': player1.total_poison_dealt,
        'poison_dealt2': player2.total_poison_dealt,
        'bleed_dealt1': player1.total_bleed_dealt,
        'bleed_dealt2': player2.total_bleed_dealt,
        'heal_received1': player1.total_heal_received,
        'heal_received2': player2.total_heal_received,
        'poison_heal_received1': player1.total_poison_heal_received,
        'poison_heal_received2': player2.total_poison_heal_received,
        'bleed_heal_received1': player1.total_bleed_heal_received,
        'bleed_heal_received2': player2.total_bleed_heal_received,
        'trash1': trash1,
        'trash2': trash2,
        'trash_this1': trash_this1,
        'trash_this2': trash_this2,
        'gear_stats1': gear_stats1,
        'gear_stats2': gear_stats2,
        'detailed_log': detailed_log,
    }


# --- Массовый анализ ---
def run_tournament(patterns, num_games=100, max_turns=30):
    results = {}
    for pat1 in patterns:
        for pat2 in patterns:
            win1 = win2 = 0
            turns_list = []
            for _ in range(num_games):
                res = simulate_game(pat1, pat2, log=False, max_turns=max_turns)
                turns_list.append(res['turns'])
                if res['winner'] == 'P1':
                    win1 += 1
                elif res['winner'] == 'P2':
                    win2 += 1
            avg_turns = sum(turns_list) / len(turns_list)
            results[(pat1, pat2)] = {
                'win1': win1, 'win2': win2, 'avg_turns': avg_turns
            }
    print("\n=== Сравнение стратегий ===")
    for (pat1, pat2), res in results.items():
        print(f"{pat1} vs {pat2}: P1 win {res['win1']}, P2 win {res['win2']}, Avg Turns: {res['avg_turns']:.1f}")

    # detailed_log только для одной игры
    if num_games == 1:
        results['detailed_log'] = res.get('detailed_log')
        # Добавляем итоговые колоды игроков
        from collections import Counter
        def deck_summary(player):
            all_cards = [c.name for c in (player.deck + player.hand + player.discard)]
            return dict(Counter(all_cards))
        results['final_decks'] = {
            'P1': deck_summary(res['player1']),
            'P2': deck_summary(res['player2'])
        }

# if __name__ == "__main__":
#     # Только одна стратегия: red vs red
#     print("\n=== Пример одной партии (red vs red) ===")
#     result = simulate_game("red", "red", log=True)
#     if result['winner']:
#         print(f"\nИгра завершена! Победил игрок {result['winner']} за {result['turns']} ходов.")
#     else:
#         print(f"\nИгра завершена! Ничья или лимит ходов. Ходов: {result['turns']}")
#
#     # --- Итоговая статистика ---
#     def player_stats(player, label):
#         all_cards = player.deck + player.discard + player.hand
#         counter = Counter([c.name for c in all_cards])
#         starters = sum(counter.get(x, 0) for x in ['Prayer', 'Strike'])
#         print(f"\n[{label}] HP={player.health}, Poison={player.poison}, Bleed={player.bleed}")
#         print(f"  Deck: {len(player.deck)}, Discard: {len(player.discard)}, Hand: {len(player.hand)}, Total: {len(all_cards)}")
#         print(f"  Стартовых карт (Prayer+Strike): {starters}")
#         print(f"  Состав всей колоды:")
#         for name, count in counter.most_common():
#             print(f"    {name}: {count}")
#         # Trash pile
#         trash_counter = Counter([c.name for c in player.trash_pile])
#         print(f"  В трэш отправлено: {sum(trash_counter.values())}")
#         if trash_counter:
#             for name, count in trash_counter.most_common():
#                 print(f"    {name}: {count}")
#
#     player_stats(result['player1'], "P1")
#     player_stats(result['player2'], "P2")
#
#     print("\n=== Массовый анализ: только red vs red (1000 игр, P1 всегда первый) ===")
#     win1 = win2 = 0
#     turns_list = []
#     p1_hp = []
#     p2_hp = []
#     p1_bleed = []
#     p2_bleed = []
#     p1_poison = []
#     p2_poison = []
#     p1_deck = []
#     p2_deck = []
#     p1_hand = []
#     p2_hand = []
#     p1_discard = []
#     p2_discard = []
#     p1_trash = []
#     p2_trash = []
#     p1_trash_this = []
#     p2_trash_this = []
#     winner_cards = []
#     loser_cards = []
#     all_games = []
#     for i in range(1000):
#         res = simulate_game("red", "red", log=False)
#         turns_list.append(res['turns'])
#         if res['winner'] == 'P1':
#             win1 += 1
#         elif res['winner'] == 'P2':
#             win2 += 1
#         p1 = res['player1']
#         p2 = res['player2']
#         p1_hp.append(p1.health)
#         p2_hp.append(p2.health)
#         p1_bleed.append(p1.bleed)
#         p2_bleed.append(p2.bleed)
#         p1_poison.append(p1.poison)
#         p2_poison.append(p2.poison)
#         p1_deck.append(len(p1.deck))
#         p2_deck.append(len(p2.deck))
#         p1_hand.append(len(p1.hand))
#         p2_hand.append(len(p2.hand))
#         p1_discard.append(len(p1.discard))
#         p2_discard.append(len(p2.discard))
#         # Trash stats
#         p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         # Для анализа карт
#         all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
#         all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
#         if res['winner'] == 'P1':
#             winner_cards.extend(all_cards_p1)
#             loser_cards.extend(all_cards_p2)
#         elif res['winner'] == 'P2':
#             winner_cards.extend(all_cards_p2)
#             loser_cards.extend(all_cards_p1)
#         all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
#     avg_turns = sum(turns_list) / len(turns_list)
#     print(f"red vs red: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
#     print(f"Средние финальные статусы:")
#     print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
#     print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
#     print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
#     print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
#     print(f"\nТоп-10 карт в колодах победителей:")
#     for name, count in Counter(winner_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print(f"\nТоп-10 карт в колодах проигравших:")
#     for name, count in Counter(loser_cards).most_common(10):
#         print(f"  {name}: {count}")
#     # Анализ самых долгих партий
#     all_games.sort(key=lambda g: -g['turns'])
#     long_games = all_games[:100]  # 100 самых долгих партий
#     long_cards = []
#     for g in long_games:
#         long_cards.extend(g['p1'])
#         long_cards.extend(g['p2'])
#     print(f"\nТоп-10 карт в самых долгих партиях:")
#     for name, count in Counter(long_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print("\n=== Анализ баланса: 1000 игр с равным HP, затем подбор разницы HP только для первого игрока (1000 игр на каждую разницу, P1 всегда первый) ===")
#     print("\nТаблица по разнице HP:")
#     print("  diff |  P1 win |  P2 win | Avg Turns")
#     print("--------------------------------------")
#     # 1000 игр с равным HP
#     win1 = win2 = 0
#     turns = 0
#     for i in range(1000):
#         res = simulate_game("red", "red", log=False)
#         if res['winner'] == 'P1':
#             win1 += 1
#         elif res['winner'] == 'P2':
#             win2 += 1
#         turns += res['turns']
#     print(f"   0   |  {win1:6d} |  {win2:6d} |   {turns/1000:.2f}")
#     # Для каждой разницы HP только у первого игрока
#     best_diff = None
#     best_gap = 1000
#     for diff in range(1, 21):
#         win1 = win2 = 0
#         turns = 0
#         custom_hp = (50 - diff, 50)
#         for i in range(1000):
#             res = simulate_game("red", "red", log=False, custom_hp=custom_hp)
#             if res['winner'] == 'P1':
#                 win1 += 1
#             elif res['winner'] == 'P2':
#                 win2 += 1
#             turns += res['turns']
#         gap = abs(win1 - win2)
#         print(f"  -{diff:2d}  |  {win1:6d} |  {win2:6d} |   {turns/1000:.2f}")
#         if gap < best_gap:
#             best_gap = gap
#             best_diff = diff
#     print(f"\nОптимальная разница HP для баланса (только у P1, P1 всегда первый): {best_diff} (разница побед: {best_gap})")
#     print("\n=== Массовый анализ: только random vs random (10000 игр, P1=40 HP, P2=50 HP) ===")
#     win1 = win2 = 0
#     poison_win1 = poison_win2 = 0
#     turns_list = []
#     p1_hp = []
#     p2_hp = []
#     p1_bleed = []
#     p2_bleed = []
#     p1_poison = []
#     p2_poison = []
#     p1_deck = []
#     p2_deck = []
#     p1_hand = []
#     p2_hand = []
#     p1_discard = []
#     p2_discard = []
#     p1_trash = []
#     p2_trash = []
#     p1_trash_this = []
#     p2_trash_this = []
#     winner_cards = []
#     loser_cards = []
#     all_games = []
#     for i in range(10000):
#         res = simulate_game("random", "random", log=False, custom_hp=(40, 50))
#         turns_list.append(res['turns'])
#         if res['winner'] == 'P1':
#             win1 += 1
#             if res['player2'].poison >= 20:
#                 poison_win1 += 1
#         elif res['winner'] == 'P2':
#             win2 += 1
#             if res['player1'].poison >= 20:
#                 poison_win2 += 1
#         p1 = res['player1']
#         p2 = res['player2']
#         p1_hp.append(p1.health)
#         p2_hp.append(p2.health)
#         p1_bleed.append(p1.bleed)
#         p2_bleed.append(p2.bleed)
#         p1_poison.append(p1.poison)
#         p2_poison.append(p2.poison)
#         p1_deck.append(len(p1.deck))
#         p2_deck.append(len(p2.deck))
#         p1_hand.append(len(p1.hand))
#         p2_hand.append(len(p2.hand))
#         p1_discard.append(len(p1.discard))
#         p2_discard.append(len(p2.discard))
#         # Trash stats
#         p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         # Для анализа карт
#         all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
#         all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
#         if res['winner'] == 'P1':
#             winner_cards.extend(all_cards_p1)
#             loser_cards.extend(all_cards_p2)
#         elif res['winner'] == 'P2':
#             winner_cards.extend(all_cards_p2)
#             loser_cards.extend(all_cards_p1)
#         all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
#     avg_turns = sum(turns_list) / len(turns_list)
#     print(f"random vs random: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
#     print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
#     print(f"Средние финальные статусы:")
#     print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
#     print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
#     print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
#     print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
#     print(f"\nТоп-10 карт в колодах победителей:")
#     for name, count in Counter(winner_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print(f"\nТоп-10 карт в колодах проигравших:")
#     for name, count in Counter(loser_cards).most_common(10):
#         print(f"  {name}: {count}")
#     # Анализ самых долгих партий
#     all_games.sort(key=lambda g: -g['turns'])
#     long_games = all_games[:100]  # 100 самых долгих партий
#     long_cards = []
#     for g in long_games:
#         long_cards.extend(g['p1'])
#         long_cards.extend(g['p2'])
#     print(f"\nТоп-10 карт в самых долгих партиях:")
#     for name, count in Counter(long_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print("\n=== Массовый анализ: poison vs red (10000 игр, P1=40 HP, P2=50 HP) ===")
#     win1 = win2 = 0
#     poison_win1 = poison_win2 = 0
#     turns_list = []
#     p1_hp = []
#     p2_hp = []
#     p1_bleed = []
#     p2_bleed = []
#     p1_poison = []
#     p2_poison = []
#     p1_deck = []
#     p2_deck = []
#     p1_hand = []
#     p2_hand = []
#     p1_discard = []
#     p2_discard = []
#     p1_trash = []
#     p2_trash = []
#     p1_trash_this = []
#     p2_trash_this = []
#     winner_cards = []
#     loser_cards = []
#     all_games = []
#     for i in range(10000):
#         res = simulate_game("poison", "red", log=False, custom_hp=(40, 50))
#         turns_list.append(res['turns'])
#         if res['winner'] == 'P1':
#             win1 += 1
#             if res['player2'].poison >= 20:
#                 poison_win1 += 1
#         elif res['winner'] == 'P2':
#             win2 += 1
#             if res['player1'].poison >= 20:
#                 poison_win2 += 1
#         p1 = res['player1']
#         p2 = res['player2']
#         p1_hp.append(p1.health)
#         p2_hp.append(p2.health)
#         p1_bleed.append(p1.bleed)
#         p2_bleed.append(p2.bleed)
#         p1_poison.append(p1.poison)
#         p2_poison.append(p2.poison)
#         p1_deck.append(len(p1.deck))
#         p2_deck.append(len(p2.deck))
#         p1_hand.append(len(p1.hand))
#         p2_hand.append(len(p2.hand))
#         p1_discard.append(len(p1.discard))
#         p2_discard.append(len(p2.discard))
#         # Trash stats
#         p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         # Для анализа карт
#         all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
#         all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
#         if res['winner'] == 'P1':
#             winner_cards.extend(all_cards_p1)
#             loser_cards.extend(all_cards_p2)
#         elif res['winner'] == 'P2':
#             winner_cards.extend(all_cards_p2)
#             loser_cards.extend(all_cards_p1)
#         all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
#     avg_turns = sum(turns_list) / len(turns_list)
#     print(f"poison vs red: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
#     print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
#     print(f"Средние финальные статусы:")
#     print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
#     print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
#     print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
#     print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
#     print(f"\nТоп-10 карт в колодах победителей:")
#     for name, count in Counter(winner_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print(f"\nТоп-10 карт в колодах проигравших:")
#     for name, count in Counter(loser_cards).most_common(10):
#         print(f"  {name}: {count}")
#     # Анализ самых долгих партий
#     all_games.sort(key=lambda g: -g['turns'])
#     long_games = all_games[:100]  # 100 самых долгих партий
#     long_cards = []
#     for g in long_games:
#         long_cards.extend(g['p1'])
#         long_cards.extend(g['p2'])
#     print(f"\nТоп-10 карт в самых долгих партиях:")
#     for name, count in Counter(long_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print("\n=== Массовый анализ: red vs poison (10000 игр, P1=40 HP, P2=50 HP) ===")
#     win1 = win2 = 0
#     poison_win1 = poison_win2 = 0
#     turns_list = []
#     p1_hp = []
#     p2_hp = []
#     p1_bleed = []
#     p2_bleed = []
#     p1_poison = []
#     p2_poison = []
#     p1_deck = []
#     p2_deck = []
#     p1_hand = []
#     p2_hand = []
#     p1_discard = []
#     p2_discard = []
#     p1_trash = []
#     p2_trash = []
#     p1_trash_this = []
#     p2_trash_this = []
#     winner_cards = []
#     loser_cards = []
#     all_games = []
#     for i in range(10000):
#         res = simulate_game("red", "poison", log=False, custom_hp=(40, 50))
#         turns_list.append(res['turns'])
#         if res['winner'] == 'P1':
#             win1 += 1
#             if res['player2'].poison >= 20:
#                 poison_win1 += 1
#         elif res['winner'] == 'P2':
#             win2 += 1
#             if res['player1'].poison >= 20:
#                 poison_win2 += 1
#         p1 = res['player1']
#         p2 = res['player2']
#         p1_hp.append(p1.health)
#         p2_hp.append(p2.health)
#         p1_bleed.append(p1.bleed)
#         p2_bleed.append(p2.bleed)
#         p1_poison.append(p1.poison)
#         p2_poison.append(p2.poison)
#         p1_deck.append(len(p1.deck))
#         p2_deck.append(len(p2.deck))
#         p1_hand.append(len(p1.hand))
#         p2_hand.append(len(p2.hand))
#         p1_discard.append(len(p1.discard))
#         p2_discard.append(len(p2.discard))
#         # Trash stats
#         p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
#         p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
#         # Для анализа карт
#         all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
#         all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
#         if res['winner'] == 'P1':
#             winner_cards.extend(all_cards_p1)
#             loser_cards.extend(all_cards_p2)
#         elif res['winner'] == 'P2':
#             winner_cards.extend(all_cards_p2)
#             loser_cards.extend(all_cards_p1)
#         all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
#     avg_turns = sum(turns_list) / len(turns_list)
#     print(f"red vs poison: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
#     print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
#     print(f"Средние финальные статусы:")
#     print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
#     print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
#     print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
#     print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
#     print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
#     print(f"\nТоп-10 карт в колодах победителей:")
#     for name, count in Counter(winner_cards).most_common(10):
#         print(f"  {name}: {count}")
#     print(f"\nТоп-10 карт в колодах проигравших:")
#     for name, count in Counter(loser_cards).most_common(10):
#         print(f"  {name}: {count}")
#     # Анализ самых долгих партий
#     all_games.sort(key=lambda g: -g['turns'])
#     long_games = all_games[:100]  # 100 самых долгих партий
#     long_cards = []
#     for g in long_games:
#         long_cards.extend(g['p1'])
#         long_cards.extend(g['p2'])
#     print(f"\nТоп-10 карт в самых долгих партиях:")
#     for name, count in Counter(long_cards).most_common(10):
#         print(f"  {name}: {count}") 