import json
import random
from collections import defaultdict, Counter
import re
import sys

# Ensure reference to the current module exists even when executed as a script
simulator = sys.modules[__name__]

# --- Глобальные переменные для Flask ---
MAIN_CARDS = None
STARTER_CARDS = None

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
    def __init__(self, name, effect1, effect2, cost, color, raw=None):
        self.name = name
        self.effect1 = effect1
        self.effect2 = effect2
        self.cost = cost
        self.color = color
        self.raw = raw or {}
        # Собираем все эффекты из effect1, effect1text, effect2, effect2text
        self.effects = []
        for key in ['effect1', 'effect1text', 'effect2', 'effect2text']:
            val = self.raw.get(key)
            if val and str(val).lower() != 'none':
                self.effects.append(str(val))
        self.absorbed_damage = 0
        self.defends = False
        self.defense = 0
        self.hp = 0
        self.is_gear = False
        # Gear определяется по всем эффектам
        for eff in self.effects:
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
            deck += [Card(card['name'], card['effect1'], card['effect2'], 0, card['color'])] * 7
        elif card['name'].lower() == 'strike':
            deck += [Card(card['name'], card['effect1'], card['effect2'], 0, card['color'])] * 3
    random.shuffle(deck)
    return deck

# --- Priestess ---
def get_priestess():
    for card in STARTER_CARDS:
        if card['name'].lower() == 'priestess':
            return Card(card['name'], card['effect1'], card['effect2'], card['cost'], card['color'])
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
    def start_turn_statuses(self):
        if self.bleed > 0:
            self.health -= self.bleed
            self.bleed = max(0, self.bleed - 1)
        if self.poison >= 20:
            return True  # проигрыш
        return False
    def apply_damage(self, dmg):
        # 1. Gear с защитой игрока (defends=True)
        for gear in list(self.gear):
            if getattr(gear, 'defends', False) and gear.defense > 0:
                absorb = min(dmg, gear.defense - gear.absorbed_damage)
                gear.absorbed_damage += absorb
                self.gear_saved_damage += absorb
                dmg -= absorb
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
            self.trade_deck += [Card(card['name'], card['effect1'], card['effect2'], card['cost'], card['color'], card['raw'])] * card['copies']
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
            self.refill_trade_row()
            return card
        return None

# --- Стратегии ---
def buy_strategy(player, market, total_blessing, spent_blessing, pattern="default", user_strategy=None):
    affordable = [(i, c) for i, c in enumerate(market.trade_row) if c.cost <= (total_blessing - spent_blessing)]
    # Если есть пользовательская стратегия
    if user_strategy and hasattr(simulator, 'get_card_priority_func'):
        priority_func, is_enabled = simulator.get_card_priority_func(user_strategy)
        # 1. Фильтрация по max_cost
        max_cost = user_strategy.get('max_cost', 20)
        affordable = [(i, c) for i, c in affordable if c.cost <= max_cost]
        # 2. Фильтрация по разрешённым картам (enabled!)
        affordable = [(i, c) for i, c in affordable if is_enabled(c)]
        # Если карта отключена (enabled: false), она не должна покупаться ни при каких условиях
        affordable = [(i, c) for i, c in affordable if getattr(c, 'name', None) and is_enabled(c)]
        if not affordable:
            return None
        # 3. Покупка Priestess только если нет других приоритетных карт
        priestess_buy_if_2 = user_strategy.get('priestess_buy_if_2')
        priestess_affordable = [(i, c) for i, c in affordable if c.name.lower() == 'priestess']
        non_priestess = [(i, c) for i, c in affordable if c.name.lower() != 'priestess']
        if priestess_affordable:
            if not non_priestess:
                return priestess_affordable[0][0]
            # Если включён режим 'покупать за 2', и у нас ровно 2 денег, и нет других приоритетных карт
            if priestess_buy_if_2:
                for i, c in priestess_affordable:
                    if c.cost == 2 and all(x[1].cost > 2 for x in non_priestess):
                        return i
        affordable = non_priestess if non_priestess else affordable
        # 4. Приоритет Trash
        if user_strategy.get('focus_trash'):
            trash_affordable = [(i, c) for i, c in affordable if 'trash' in (c.effect1.lower() + c.effect2.lower())]
            if trash_affordable:
                affordable = trash_affordable
        # 5. Приоритет по эффектам
        effect_priority = user_strategy.get('effect_priority', [])
        def effect_score(card):
            text = (card.effect1 + ' ' + card.effect2).lower()
            for idx, eff in enumerate(effect_priority):
                if eff.lower() in text:
                    return idx
            return 1000
        affordable.sort(key=lambda x: (effect_score(x[1]), priority_func(x[1])))
        return affordable[0][0]
    # Стандартная логика
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

def parse_effects(effect_str):
    """
    Парсит строку вида {Damage 3}{Draw 1} or {Stun 1} в структуру:
    - если есть 'or', возвращает список альтернатив: [[('damage', 3), ('draw', 1)], [('stun', 1)]]
    - если нет 'or', возвращает обычный список [('damage', 3), ('draw', 1)]
    """
    if not effect_str:
        return []
    # Разделяем по ' or ' (с пробелами)
    parts = [p.strip() for p in re.split(r'\s+or\s+', effect_str)]
    all_effects = []
    for part in parts:
        effects = []
        for match in re.finditer(r'\{([A-Za-z_]+)\s*([\-\d]+)?\}', part):
            name = match.group(1).lower()
            value = int(match.group(2)) if match.group(2) else 1
            effects.append((name, value))
        all_effects.append(effects)
    if len(all_effects) == 1:
        return all_effects[0]
    return all_effects  # список альтернатив

# --- Эффекты ---
def apply_card_effects(card, player, opponent, log, trash_list):
    # --- Эффект1 ---
    eff1 = parse_effects(card.effect1)
    if eff1 and isinstance(eff1[0], list):  # OR-структура
        # Выбираем вариант (пока случайно, можно по стратегии)
        chosen = random.choice(eff1)
        for eff in chosen:
            if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                continue
            name, value = eff
            apply_effect(name, value, card, player, opponent, log, trash_list)
    else:
        for eff in eff1:
            if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                continue
            name, value = eff
            apply_effect(name, value, card, player, opponent, log, trash_list)
    # --- Эффект2 ---
    eff2 = parse_effects(card.effect2)
    if eff2 and isinstance(eff2[0], list):
        chosen = random.choice(eff2)
        for eff in chosen:
            if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                continue
            name, value = eff
            apply_effect(name, value, card, player, opponent, log, trash_list)
    else:
        for eff in eff2:
            if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                continue
            name, value = eff
            apply_effect(name, value, card, player, opponent, log, trash_list)
    # Priestess trash logic
    if card.name.lower() == 'priestess':
        strat = getattr(player, 'user_strategy', None)
        trash_after = None
        if strat:
            trash_after = strat.get('priestess_trash_after')
        if trash_after is not None:
            # Уникальный id карты (по id объекта)
            cid = id(card)
            player.priestess_uses[cid] = player.priestess_uses.get(cid, 0) + 1
            if player.priestess_uses[cid] >= trash_after:
                # Трэшим Priestess
                if card in player.hand:
                    player.hand.remove(card)
                elif card in player.discard:
                    player.discard.remove(card)
                elif card in player.deck:
                    player.deck.remove(card)
                card.trashed_by = 'priestess_trash'
                player.trash_pile.append(card)
                if log:
                    print(f"  [Priestess] Карта отправлена в трэш после {trash_after} использований!")

def apply_effect(name, value, card, player, opponent, log, trash_list):
    if name == 'damage':
        opponent.apply_damage(value)
        player.total_damage_dealt += value
        if log:
            print(f"  {card.name} наносит {value} урона!")
    elif name == 'heal':
        player.heal(value)
        player.total_heal_received += value
        if log:
            print(f"  {card.name} лечит на {value}!")
    elif name == 'heal_bleed':
        player.heal_bleed(value)
        player.total_bleed_heal_received += value
        if log:
            print(f"  {card.name} снимает bleed на {value}!")
    elif name == 'heal_poison':
        player.heal_poison(value)
        player.total_poison_heal_received += value
        if log:
            print(f"  {card.name} снимает poison на {value}!")
    elif name == 'bleed':
        opponent.bleed += value
        player.total_bleed_dealt += value
        if log:
            print(f"  {card.name} даёт {value} bleed!")
    elif name == 'poison':
        opponent.poison += value
        player.total_poison_dealt += value
        if log:
            print(f"  {card.name} даёт {value} яда!")
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
            if log:
                print(f"  [Trash] Удалена стартовая карта из сброса: {c.name}")
            return None  # из сброса — не влияет на hand_queue
        # Рука
        hand_starters = [c for c in player.hand if is_starter(c)]
        if hand_starters:
            c = hand_starters[0]
            player.hand.remove(c)
            c.trashed_by = 'trash'
            if log:
                print(f"  [Trash] Удалена стартовая карта из руки: {c.name}")
            return c  # вернуть удалённую карту из руки
        if log:
            print(f"  [Trash] Нет стартовых карт для удаления")
        return None
    elif name == 'draw':
        player.draw(value)
        if log:
            print(f"  {card.name} добирает {value} карт(ы)!")
    elif name == 'stun':
        to_discard = sorted(opponent.hand, key=lambda c: c.cost)[:value]
        for c in to_discard:
            opponent.hand.remove(c)
            opponent.discard.append(c)
            if log:
                print(f"  [Stun] {opponent.name} сбрасывает карту: {c.name}")
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
                    print(f"  [Spy] {c.name} сброшена!")
        # Базовые возвращаем на колоду (в том же порядке)
        opponent.deck.extend(reversed(to_return))
        if log:
            print(f"  [Spy] Базовые карты возвращены на колоду: {[c.name for c in to_return]}")
    elif name == 'steal':
        for _ in range(value):
            if opponent.deck:
                stolen = opponent.deck.pop()
                player.discard.append(stolen)
                if log:
                    print(f"  [Steal] Украдена карта: {stolen.name}")
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
                print(f"  [Destroy] Уничтожена gear-карта: {destroyed.name}")

# --- Симуляция одной партии ---
def simulate_game(pattern1, pattern2, log=False, max_turns=30, first_player=0, custom_hp=None, collect_log=False):
    player1 = Player("P1")
    player2 = Player("P2")
    market = TradeMarket(MAIN_CARDS)
    player1.market = market
    player2.market = market
    priestess = get_priestess()
    players = [player1, player2]
    patterns = [pattern1, pattern2]
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
            print(f"\n=== Ход {turn+1} ===")
        for idx, player in enumerate(players):
            opponent = players[1 - idx]
            lost = player.start_turn_statuses()
            if log:
                print(f"Игрок {idx+1}: HP={player.health}, Poison={player.poison}, Bleed={player.bleed}, Hand={[c.name for c in player.hand]}")
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
                    print(f"Игрок {idx+1} проиграл!")
                if collect_log:
                    turn_log.append({'player': player.name, 'lost': True})
                break
            # --- Новый подсчёт Blessing по всей руке ---
            total_blessing = sum(
                eff[1]
                for card in player.hand
                for eff in (parse_effects(card.effect1) + parse_effects(card.effect2))
                if isinstance(eff, (list, tuple)) and len(eff) == 2 and eff[0] == 'blessing'
            )
            trash_list = []
            played = set()
            hand_queue = list(player.hand)
            played_this_turn = []
            effects_this_turn = []
            while hand_queue:
                card = hand_queue.pop(0)
                if card in played:
                    continue
                # --- GEAR: если карта gear, кладём на стол, эффекты применяем, но не уходит в discard ---
                if getattr(card, 'is_gear', False):
                    if not any(g.name == card.name for g in player.gear):
                        player.gear.append(card)
                    for eff in parse_effects(card.effect1):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name not in ['def_y_text', 'def_n_text']:
                            apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                    for eff in parse_effects(card.effect2):
                        if not isinstance(eff, (list, tuple)) or len(eff) != 2:
                            continue
                        name, value = eff
                        if name not in ['def_y_text', 'def_n_text']:
                            apply_effect(name, value, card, player, opponent, log, trash_list)
                            if collect_log:
                                effects_this_turn.append({'card': card.name, 'effect': name, 'value': value, 'target': opponent.name})
                    played.add(card)
                    played_this_turn.append(card.name)
                    continue
                # --- Обычная обработка ---
                def draw_hook(n):
                    player.draw(n)
                    new_cards = [c for c in player.hand if c not in played and c not in hand_queue]
                    hand_queue.extend(new_cards)
                    if log and new_cards:
                        print(f"  Добрано карт: {[c.name for c in new_cards]}")
                def apply_card_effects_with_draw(card, player, opponent, log, trash_list):
                    removed_from_hand = []
                    for eff in parse_effects(card.effect1):
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
                    for eff in parse_effects(card.effect2):
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
                    return removed_from_hand
                removed = apply_card_effects_with_draw(card, player, opponent, log, trash_list)
                for rem_card in removed:
                    hand_queue = [c for c in hand_queue if c != rem_card]
                played.add(card)
                played_this_turn.append(card.name)
            # Trash_this: удаляем карты из руки
            for card in trash_list:
                if card in player.hand:
                    player.hand.remove(card)
                    card.trashed_by = 'trash_this'
                    player.trash_pile.append(card)
            # --- Покупка карт ---
            spent_blessing = 0
            bought_cards = []
            market_state = []
            buy_steps = [] if collect_log else None
            while True:
                if log:
                    print(f"  Blessing на ход: {total_blessing}, потрачено: {spent_blessing}")
                    print(f"  Доступные для покупки: {[c.name for c in market.trade_row if c.cost <= (total_blessing - spent_blessing)]}")
                i = buy_strategy(player, market, total_blessing, spent_blessing, pattern=patterns[idx])
                if i is None:
                    max_priestess = 0
                    if priestess and priestess.cost > 0:
                        max_priestess = (total_blessing - spent_blessing) // priestess.cost
                    if max_priestess > 0:
                        for _ in range(max_priestess):
                            if collect_log:
                                buy_steps.append({'before': total_blessing - spent_blessing, 'card': 'Priestess', 'cost': priestess.cost, 'after': total_blessing - spent_blessing - priestess.cost})
                            player.discard.append(Card(priestess.name, priestess.effect1, priestess.effect2, priestess.cost, priestess.color))
                            spent_blessing += priestess.cost
                            bought_cards.append('Priestess')
                            if log:
                                print(f"  Куплена карта: Priestess")
                            if collect_log:
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
                                        name_count[c.name] = 0
                                market_state.append({'market': market_cards, 'buy': 'Priestess'})
                    break
                card = market.trade_row[i]
                if collect_log:
                    buy_steps.append({'before': total_blessing - spent_blessing, 'card': card.name, 'cost': card.cost, 'after': total_blessing - spent_blessing - card.cost})
                spent_blessing += card.cost
                market.buy_card(i, player)
                bought_cards.append(card.name)
                if log:
                    print(f"  Куплена карта: {card.name}")
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
                print(f"  Суммарно потрачено Blessing: {spent_blessing} из {total_blessing}")
            player.end_turn()
            if player.health <= 0 or player.poison >= 20:
                winner = opponent.name
                if log:
                    print(f"Игрок {idx+1} проиграл!")
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
                    'buy_steps': buy_steps if collect_log else None
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

if __name__ == "__main__":
    # Только одна стратегия: red vs red
    print("\n=== Пример одной партии (red vs red) ===")
    result = simulate_game("red", "red", log=True)
    if result['winner']:
        print(f"\nИгра завершена! Победил игрок {result['winner']} за {result['turns']} ходов.")
    else:
        print(f"\nИгра завершена! Ничья или лимит ходов. Ходов: {result['turns']}")

    # --- Итоговая статистика ---
    def player_stats(player, label):
        all_cards = player.deck + player.discard + player.hand
        counter = Counter([c.name for c in all_cards])
        starters = sum(counter.get(x, 0) for x in ['Prayer', 'Strike'])
        print(f"\n[{label}] HP={player.health}, Poison={player.poison}, Bleed={player.bleed}")
        print(f"  Deck: {len(player.deck)}, Discard: {len(player.discard)}, Hand: {len(player.hand)}, Total: {len(all_cards)}")
        print(f"  Стартовых карт (Prayer+Strike): {starters}")
        print(f"  Состав всей колоды:")
        for name, count in counter.most_common():
            print(f"    {name}: {count}")
        # Trash pile
        trash_counter = Counter([c.name for c in player.trash_pile])
        print(f"  В трэш отправлено: {sum(trash_counter.values())}")
        if trash_counter:
            for name, count in trash_counter.most_common():
                print(f"    {name}: {count}")

    player_stats(result['player1'], "P1")
    player_stats(result['player2'], "P2")

    print("\n=== Массовый анализ: только red vs red (1000 игр, P1 всегда первый) ===")
    win1 = win2 = 0
    turns_list = []
    p1_hp = []
    p2_hp = []
    p1_bleed = []
    p2_bleed = []
    p1_poison = []
    p2_poison = []
    p1_deck = []
    p2_deck = []
    p1_hand = []
    p2_hand = []
    p1_discard = []
    p2_discard = []
    p1_trash = []
    p2_trash = []
    p1_trash_this = []
    p2_trash_this = []
    winner_cards = []
    loser_cards = []
    all_games = []
    for i in range(1000):
        res = simulate_game("red", "red", log=False)
        turns_list.append(res['turns'])
        if res['winner'] == 'P1':
            win1 += 1
        elif res['winner'] == 'P2':
            win2 += 1
        p1 = res['player1']
        p2 = res['player2']
        p1_hp.append(p1.health)
        p2_hp.append(p2.health)
        p1_bleed.append(p1.bleed)
        p2_bleed.append(p2.bleed)
        p1_poison.append(p1.poison)
        p2_poison.append(p2.poison)
        p1_deck.append(len(p1.deck))
        p2_deck.append(len(p2.deck))
        p1_hand.append(len(p1.hand))
        p2_hand.append(len(p2.hand))
        p1_discard.append(len(p1.discard))
        p2_discard.append(len(p2.discard))
        # Trash stats
        p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        # Для анализа карт
        all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
        all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
        if res['winner'] == 'P1':
            winner_cards.extend(all_cards_p1)
            loser_cards.extend(all_cards_p2)
        elif res['winner'] == 'P2':
            winner_cards.extend(all_cards_p2)
            loser_cards.extend(all_cards_p1)
        all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
    avg_turns = sum(turns_list) / len(turns_list)
    print(f"red vs red: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
    print(f"Средние финальные статусы:")
    print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
    print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
    print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
    print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
    print(f"\nТоп-10 карт в колодах победителей:")
    for name, count in Counter(winner_cards).most_common(10):
        print(f"  {name}: {count}")
    print(f"\nТоп-10 карт в колодах проигравших:")
    for name, count in Counter(loser_cards).most_common(10):
        print(f"  {name}: {count}")
    # Анализ самых долгих партий
    all_games.sort(key=lambda g: -g['turns'])
    long_games = all_games[:100]  # 100 самых долгих партий
    long_cards = []
    for g in long_games:
        long_cards.extend(g['p1'])
        long_cards.extend(g['p2'])
    print(f"\nТоп-10 карт в самых долгих партиях:")
    for name, count in Counter(long_cards).most_common(10):
        print(f"  {name}: {count}")

    print("\n=== Анализ баланса: 1000 игр с равным HP, затем подбор разницы HP только для первого игрока (1000 игр на каждую разницу, P1 всегда первый) ===")
    print("\nТаблица по разнице HP:")
    print("  diff |  P1 win |  P2 win | Avg Turns")
    print("--------------------------------------")
    # 1000 игр с равным HP
    win1 = win2 = 0
    turns = 0
    for i in range(1000):
        res = simulate_game("red", "red", log=False)
        if res['winner'] == 'P1':
            win1 += 1
        elif res['winner'] == 'P2':
            win2 += 1
        turns += res['turns']
    print(f"   0   |  {win1:6d} |  {win2:6d} |   {turns/1000:.2f}")
    # Для каждой разницы HP только у первого игрока
    best_diff = None
    best_gap = 1000
    for diff in range(1, 21):
        win1 = win2 = 0
        turns = 0
        custom_hp = (50 - diff, 50)
        for i in range(1000):
            res = simulate_game("red", "red", log=False, custom_hp=custom_hp)
            if res['winner'] == 'P1':
                win1 += 1
            elif res['winner'] == 'P2':
                win2 += 1
            turns += res['turns']
        gap = abs(win1 - win2)
        print(f"  -{diff:2d}  |  {win1:6d} |  {win2:6d} |   {turns/1000:.2f}")
        if gap < best_gap:
            best_gap = gap
            best_diff = diff
    print(f"\nОптимальная разница HP для баланса (только у P1, P1 всегда первый): {best_diff} (разница побед: {best_gap})")

    print("\n=== Массовый анализ: только random vs random (10000 игр, P1=40 HP, P2=50 HP) ===")
    win1 = win2 = 0
    poison_win1 = poison_win2 = 0
    turns_list = []
    p1_hp = []
    p2_hp = []
    p1_bleed = []
    p2_bleed = []
    p1_poison = []
    p2_poison = []
    p1_deck = []
    p2_deck = []
    p1_hand = []
    p2_hand = []
    p1_discard = []
    p2_discard = []
    p1_trash = []
    p2_trash = []
    p1_trash_this = []
    p2_trash_this = []
    winner_cards = []
    loser_cards = []
    all_games = []
    for i in range(10000):
        res = simulate_game("random", "random", log=False, custom_hp=(40, 50))
        turns_list.append(res['turns'])
        if res['winner'] == 'P1':
            win1 += 1
            if res['player2'].poison >= 20:
                poison_win1 += 1
        elif res['winner'] == 'P2':
            win2 += 1
            if res['player1'].poison >= 20:
                poison_win2 += 1
        p1 = res['player1']
        p2 = res['player2']
        p1_hp.append(p1.health)
        p2_hp.append(p2.health)
        p1_bleed.append(p1.bleed)
        p2_bleed.append(p2.bleed)
        p1_poison.append(p1.poison)
        p2_poison.append(p2.poison)
        p1_deck.append(len(p1.deck))
        p2_deck.append(len(p2.deck))
        p1_hand.append(len(p1.hand))
        p2_hand.append(len(p2.hand))
        p1_discard.append(len(p1.discard))
        p2_discard.append(len(p2.discard))
        # Trash stats
        p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        # Для анализа карт
        all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
        all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
        if res['winner'] == 'P1':
            winner_cards.extend(all_cards_p1)
            loser_cards.extend(all_cards_p2)
        elif res['winner'] == 'P2':
            winner_cards.extend(all_cards_p2)
            loser_cards.extend(all_cards_p1)
        all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
    avg_turns = sum(turns_list) / len(turns_list)
    print(f"random vs random: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
    print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
    print(f"Средние финальные статусы:")
    print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
    print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
    print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
    print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
    print(f"\nТоп-10 карт в колодах победителей:")
    for name, count in Counter(winner_cards).most_common(10):
        print(f"  {name}: {count}")
    print(f"\nТоп-10 карт в колодах проигравших:")
    for name, count in Counter(loser_cards).most_common(10):
        print(f"  {name}: {count}")
    # Анализ самых долгих партий
    all_games.sort(key=lambda g: -g['turns'])
    long_games = all_games[:100]  # 100 самых долгих партий
    long_cards = []
    for g in long_games:
        long_cards.extend(g['p1'])
        long_cards.extend(g['p2'])
    print(f"\nТоп-10 карт в самых долгих партиях:")
    for name, count in Counter(long_cards).most_common(10):
        print(f"  {name}: {count}")

    print("\n=== Массовый анализ: poison vs red (10000 игр, P1=40 HP, P2=50 HP) ===")
    win1 = win2 = 0
    poison_win1 = poison_win2 = 0
    turns_list = []
    p1_hp = []
    p2_hp = []
    p1_bleed = []
    p2_bleed = []
    p1_poison = []
    p2_poison = []
    p1_deck = []
    p2_deck = []
    p1_hand = []
    p2_hand = []
    p1_discard = []
    p2_discard = []
    p1_trash = []
    p2_trash = []
    p1_trash_this = []
    p2_trash_this = []
    winner_cards = []
    loser_cards = []
    all_games = []
    for i in range(10000):
        res = simulate_game("poison", "red", log=False, custom_hp=(40, 50))
        turns_list.append(res['turns'])
        if res['winner'] == 'P1':
            win1 += 1
            if res['player2'].poison >= 20:
                poison_win1 += 1
        elif res['winner'] == 'P2':
            win2 += 1
            if res['player1'].poison >= 20:
                poison_win2 += 1
        p1 = res['player1']
        p2 = res['player2']
        p1_hp.append(p1.health)
        p2_hp.append(p2.health)
        p1_bleed.append(p1.bleed)
        p2_bleed.append(p2.bleed)
        p1_poison.append(p1.poison)
        p2_poison.append(p2.poison)
        p1_deck.append(len(p1.deck))
        p2_deck.append(len(p2.deck))
        p1_hand.append(len(p1.hand))
        p2_hand.append(len(p2.hand))
        p1_discard.append(len(p1.discard))
        p2_discard.append(len(p2.discard))
        # Trash stats
        p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        # Для анализа карт
        all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
        all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
        if res['winner'] == 'P1':
            winner_cards.extend(all_cards_p1)
            loser_cards.extend(all_cards_p2)
        elif res['winner'] == 'P2':
            winner_cards.extend(all_cards_p2)
            loser_cards.extend(all_cards_p1)
        all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
    avg_turns = sum(turns_list) / len(turns_list)
    print(f"poison vs red: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
    print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
    print(f"Средние финальные статусы:")
    print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
    print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
    print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
    print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
    print(f"\nТоп-10 карт в колодах победителей:")
    for name, count in Counter(winner_cards).most_common(10):
        print(f"  {name}: {count}")
    print(f"\nТоп-10 карт в колодах проигравших:")
    for name, count in Counter(loser_cards).most_common(10):
        print(f"  {name}: {count}")
    # Анализ самых долгих партий
    all_games.sort(key=lambda g: -g['turns'])
    long_games = all_games[:100]  # 100 самых долгих партий
    long_cards = []
    for g in long_games:
        long_cards.extend(g['p1'])
        long_cards.extend(g['p2'])
    print(f"\nТоп-10 карт в самых долгих партиях:")
    for name, count in Counter(long_cards).most_common(10):
        print(f"  {name}: {count}")

    print("\n=== Массовый анализ: red vs poison (10000 игр, P1=40 HP, P2=50 HP) ===")
    win1 = win2 = 0
    poison_win1 = poison_win2 = 0
    turns_list = []
    p1_hp = []
    p2_hp = []
    p1_bleed = []
    p2_bleed = []
    p1_poison = []
    p2_poison = []
    p1_deck = []
    p2_deck = []
    p1_hand = []
    p2_hand = []
    p1_discard = []
    p2_discard = []
    p1_trash = []
    p2_trash = []
    p1_trash_this = []
    p2_trash_this = []
    winner_cards = []
    loser_cards = []
    all_games = []
    for i in range(10000):
        res = simulate_game("red", "poison", log=False, custom_hp=(40, 50))
        turns_list.append(res['turns'])
        if res['winner'] == 'P1':
            win1 += 1
            if res['player2'].poison >= 20:
                poison_win1 += 1
        elif res['winner'] == 'P2':
            win2 += 1
            if res['player1'].poison >= 20:
                poison_win2 += 1
        p1 = res['player1']
        p2 = res['player2']
        p1_hp.append(p1.health)
        p2_hp.append(p2.health)
        p1_bleed.append(p1.bleed)
        p2_bleed.append(p2.bleed)
        p1_poison.append(p1.poison)
        p2_poison.append(p2.poison)
        p1_deck.append(len(p1.deck))
        p2_deck.append(len(p2.deck))
        p1_hand.append(len(p1.hand))
        p2_hand.append(len(p2.hand))
        p1_discard.append(len(p1.discard))
        p2_discard.append(len(p2.discard))
        # Trash stats
        p1_trash.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p2_trash.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash'))
        p1_trash_this.append(sum(1 for c in p1.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        p2_trash_this.append(sum(1 for c in p2.trash_pile if getattr(c, 'trashed_by', None) == 'trash_this'))
        # Для анализа карт
        all_cards_p1 = [c.name for c in (p1.deck + p1.discard + p1.hand) if c.name not in ['Prayer', 'Strike']]
        all_cards_p2 = [c.name for c in (p2.deck + p2.discard + p2.hand) if c.name not in ['Prayer', 'Strike']]
        if res['winner'] == 'P1':
            winner_cards.extend(all_cards_p1)
            loser_cards.extend(all_cards_p2)
        elif res['winner'] == 'P2':
            winner_cards.extend(all_cards_p2)
            loser_cards.extend(all_cards_p1)
        all_games.append({'turns': res['turns'], 'p1': all_cards_p1, 'p2': all_cards_p2})
    avg_turns = sum(turns_list) / len(turns_list)
    print(f"red vs poison: P1 win {win1}, P2 win {win2}, Avg Turns: {avg_turns:.2f}")
    print(f"Побед через яд: P1={poison_win1}, P2={poison_win2}")
    print(f"Средние финальные статусы:")
    print(f"  P1: HP={sum(p1_hp)/len(p1_hp):.1f}, Bleed={sum(p1_bleed)/len(p1_bleed):.2f}, Poison={sum(p1_poison)/len(p1_poison):.2f}, Deck={sum(p1_deck)/len(p1_deck):.2f}, Hand={sum(p1_hand)/len(p1_hand):.2f}, Discard={sum(p1_discard)/len(p1_discard):.2f}")
    print(f"     Trash: по trash={sum(p1_trash)/len(p1_trash):.2f}, по trash_this={sum(p1_trash_this)/len(p1_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p1_trash)}, по trash_this={sum(p1_trash_this)}")
    print(f"  P2: HP={sum(p2_hp)/len(p2_hp):.1f}, Bleed={sum(p2_bleed)/len(p2_bleed):.2f}, Poison={sum(p2_poison)/len(p2_poison):.2f}, Deck={sum(p2_deck)/len(p2_deck):.2f}, Hand={sum(p2_hand)/len(p2_hand):.2f}, Discard={sum(p2_discard)/len(p2_discard):.2f}")
    print(f"     Trash: по trash={sum(p2_trash)/len(p2_trash):.2f}, по trash_this={sum(p2_trash_this)/len(p2_trash_this):.2f}")
    print(f"     Trash (суммарно): по trash={sum(p2_trash)}, по trash_this={sum(p2_trash_this)}")
    print(f"\nТоп-10 карт в колодах победителей:")
    for name, count in Counter(winner_cards).most_common(10):
        print(f"  {name}: {count}")
    print(f"\nТоп-10 карт в колодах проигравших:")
    for name, count in Counter(loser_cards).most_common(10):
        print(f"  {name}: {count}")
    # Анализ самых долгих партий
    all_games.sort(key=lambda g: -g['turns'])
    long_games = all_games[:100]  # 100 самых долгих партий
    long_cards = []
    for g in long_games:
        long_cards.extend(g['p1'])
        long_cards.extend(g['p2'])
    print(f"\nТоп-10 карт в самых долгих партиях:")
    for name, count in Counter(long_cards).most_common(10):
        print(f"  {name}: {count}") 