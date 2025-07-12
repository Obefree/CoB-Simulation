from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import os
import pandas as pd
import math
import numpy as np
import json
import sys
import datetime
import traceback
from collections import Counter
import re # Added for parsing effect strings

# Append path to an external simulator if provided via environment variable
SIMULATOR_ENV_VAR = "SIMULATOR_PATH"
simulator_path = os.getenv(SIMULATOR_ENV_VAR)
if simulator_path:
    sys.path.append(simulator_path)
from simulator import simulate_game, Card

app = Flask(__name__)
CORS(app)

CARDS_CSV = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRZQwtU_n44GNrXPXOWMJSYIiw_bbSdbQ224k58hy6pCPXIb65Cl4gcuNzhTvPQEpthwduWBI5ndPtX/pub?gid=1628155421&single=true&output=csv'
STARTER_CSV = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRZQwtU_n44GNrXPXOWMJSYIiw_bbSdbQ224k58hy6pCPXIb65Cl4gcuNzhTvPQEpthwduWBI5ndPtX/pub?gid=0&single=true&output=csv'
# Добавляем ссылку на таблицу с эффектами/весами
EFFECTS_CSV = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRZQwtU_n44GNrXPXOWMJSYIiw_bbSdbQ224k58hy6pCPXIb65Cl4gcuNzhTvPQEpthwduWBI5ndPtX/pub?gid=700597969&single=true&output=csv'

# Функция fix_nan больше не нужна, так как мы убрали поле 'raw'

def parse_main_cards():
    import re
    df = pd.read_csv(CARDS_CSV)
    df = df.where(pd.notnull(df), None)
    # Загружаем эффекты/веса (abilities) из второго файла
    try:
        df_eff = pd.read_csv(EFFECTS_CSV)
        # abilities: все столбцы по имени карты
        eff_map = {}
        for _, row in df_eff.iterrows():
            name = str(row.get('Name', '')).strip()
            if not name:
                continue
            eff_map[name] = {k: v for k, v in row.items() if pd.notnull(v)}
    except Exception as e:
        eff_map = {}
    cards = []
    for _, row in df.iterrows():
        card_name = str(row.get('Name', '')).strip()
        # Явно берём эффекты из D, E, F, G
        effect1 = str(row.get('effect1', '')).strip()
        effect1text = str(row.get('effect1text', '')).strip()
        effect2 = str(row.get('effect2', '')).strip()
        effect2text = str(row.get('effect2text', '')).strip()
        # Добавляем {Def_Y_Text N} и {Def_N_Text N} в effect1/effect2, если есть такие столбцы
        def_y = row.get('Def_Y_Text')
        def_n = row.get('Def_N_Text')
        # Проверяем, что значения не пустые и не nan
        if def_y is not None and str(def_y).strip() and str(def_y).lower() != 'nan':
            try:
                val = int(float(def_y))
                if val > 0 and f'{{Def_Y_Text {val}}}' not in effect1:
                    if effect1:
                        effect1 = f"{{Def_Y_Text {val}}} " + effect1
                    else:
                        effect1 = f"{{Def_Y_Text {val}}}"
            except Exception:
                pass
        if def_n is not None and str(def_n).strip() and str(def_n).lower() != 'nan':
            try:
                val = int(float(def_n))
                if val > 0 and f'{{Def_N_Text {val}}}' not in effect2:
                    if effect2:
                        effect2 = f"{{Def_N_Text {val}}} " + effect2
                    else:
                        effect2 = f"{{Def_N_Text {val}}}"
            except Exception:
                pass
        # --- Новый устойчивый парсер стоимости ---
        cost_val = row.get('Cost_Bless', '0')
        try:
            if cost_val is None or str(cost_val).strip() == '' or str(cost_val).lower() == 'nan':
                cost = 0
            else:
                m = re.search(r'\d+', str(cost_val))
                cost = int(m.group(0)) if m else 0
        except Exception:
            cost = 0
        card = {
            'name': card_name,
            'type': str(row.get('Type', '')).strip(),
            'color': str(row.get('Color', '')).replace('{','').replace('}','').strip(),
            'cost': cost,
            'effect1': effect1,
            'effect1text': effect1text,
            'effect2': effect2,
            'effect2text': effect2text,
            'copies': int(row.get('Copies', 1)) if str(row.get('Copies', '')).strip() else 1
        }
        if card['type'].lower() == 'gear':
            card['is_gear'] = True
        # abilities — все столбцы из EFFECTS_CSV
        if card_name in eff_map:
            card['abilities'] = eff_map[card_name]
        cards.append(card)
    return cards

def parse_starters():
    import re
    df = pd.read_csv(STARTER_CSV)
    df = df.where(pd.notnull(df), None)
    starters = []
    for _, row in df.iterrows():
        cost_val = row.get('Cost_Bless', 0)
        try:
            if cost_val is None or str(cost_val).strip() == '' or str(cost_val).lower() == 'nan':
                cost = 0
            else:
                # Извлекаем только первое целое число из строки
                m = re.search(r'\d+', str(cost_val))
                cost = int(m.group(0)) if m else 0
        except Exception:
            cost = 0
        starter = {
            'name': str(row.get('Name', '')).strip(),
            'color': str(row.get('Color', '')).replace('{','').replace('}','').strip(),
            'cost': cost,
            'effect1': str(row.get('Effect1', '')).strip(),
            'effect1text': str(row.get('Effect1Text', '')).strip() if 'Effect1Text' in row else '',
            'effect2': str(row.get('Effect2', '')).strip(),
            'effect2text': str(row.get('Effect2Text', '')).strip() if 'Effect2Text' in row else '',
            'copies': int(row.get('Copies', 1)) if str(row.get('Copies', '')).strip() else 1
        }
        # --- Форсируем эффект Blessing для Prayer, если он вдруг потерялся ---
        if starter['name'].lower() == 'prayer' and not starter['effect1'] and not starter['effect1text']:
            starter['effect1'] = '{Blessing 1}'
        if starter['name'].lower() == 'prayer':
            pass # Removed print('DEBUG PRAYER:', starter)
        starters.append(starter)
    return starters

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/api/cards', methods=['GET'])
def get_cards():
    main = parse_main_cards()
    starters = parse_starters()
    return jsonify({'main': main, 'starters': starters})

@app.route('/api/simulate', methods=['POST'])
def simulate():
    try:
        data = request.json
        strategy1 = data.get('strategy1', 'red')
        strategy2 = data.get('strategy2', 'poison')
        user_strategy1 = data.get('user_strategy1')
        user_strategy2 = data.get('user_strategy2')
        hp1 = int(data.get('hp1', 40))
        hp2 = int(data.get('hp2', 50))
        num_games = int(data.get('num_games', 1))  # было 1000, теперь 1
        enabled_cards = set(name.strip().lower() for name in data.get('enabled_cards', []))
        log_options = data.get('log_options', None)
        all_cards = parse_main_cards()
        filtered_cards = [c for c in all_cards if c['name'].strip().lower() in enabled_cards]
        starter_names = set(card['name'] for card in parse_starters())

        def get_card_priority_func(user_strategy):
            if not user_strategy:
                return None
            # Собираем приоритеты: {card_name: (priority, enabled)}
            card_priority = {}
            for color, arr in user_strategy.get('cards', {}).items():
                for idx, obj in enumerate(arr):
                    card_priority[obj['name'].strip().lower()] = (idx, obj.get('enabled', True))
            effect_filters = user_strategy.get('effect_filters', {})
            def priority(card):
                name = card.name.strip().lower() if hasattr(card, 'name') else card['name'].strip().lower()
                if name in card_priority:
                    prio, enabled = card_priority[name]
                    return (0 if enabled else 10000, prio)
                return (10000, 9999)
            def is_enabled(card):
                name = card.name.strip().lower() if hasattr(card, 'name') else card['name'].strip().lower()
                # 1. Проверка enabled-флага
                enabled = card_priority.get(name, (None, True))[1]
                if not enabled:
                    return False
                # 2. Больше не фильтруем по effect_filters (все карты разрешены, если не отключены явно)
                return True
            return priority, is_enabled

        def is_card_allowed(card_name, enabled_cards, user_strategy):
            name = card_name.strip().lower()
            if name not in enabled_cards:
                return False
            if user_strategy and 'cards' in user_strategy:
                for color, arr in user_strategy['cards'].items():
                    for obj in arr:
                        if obj['name'].strip().lower() == name:
                            return obj.get('enabled', True)
            return True

        # Передаём приоритеты в симулятор через глобальные переменные (или паттерн)
        import simulator as sim
        sim.MAIN_CARDS = filtered_cards
        sim.STARTER_CARDS = parse_starters()
        sim.USER_STRATEGY1 = user_strategy1
        sim.USER_STRATEGY2 = user_strategy2
        sim.STRATEGY1 = strategy1
        sim.STRATEGY2 = strategy2
        sim.get_card_priority_func = get_card_priority_func
        sim.ACTIVE_LOG_OPTIONS = set(log_options or [])

        win1 = win2 = poison_win = 0
        poison_win1 = poison_win2 = 0
        turns_list = []
        hp1_end = []
        hp2_end = []
        total_damage = 0
        total_damage1 = 0
        total_damage2 = 0
        trash_counter = {}
        gear_absorb = {}
        card_value = {}
        # Новые счетчики для анализа победителей/проигравших
        winner_card_counter = {}
        loser_card_counter = {}
        winner_hp = []
        winner_poison = []
        hp_diff = []
        # Сбор финальных значений яда и кровотока
        poison1_end = []
        poison2_end = []
        bleed1_end = []
        bleed2_end = []
        # Новые метрики по эффектам и трэшу
        sum_damage_dealt1 = sum_damage_dealt2 = 0
        sum_poison_dealt1 = sum_poison_dealt2 = 0
        sum_bleed_dealt1 = sum_bleed_dealt2 = 0
        sum_heal_received1 = sum_heal_received2 = 0
        sum_poison_heal_received1 = sum_poison_heal_received2 = 0
        sum_bleed_heal_received1 = sum_bleed_heal_received2 = 0
        sum_trash1 = sum_trash2 = 0
        sum_trash_this1 = sum_trash_this2 = 0
        # Gear-статистика по всем играм
        gear_played1 = Counter()
        gear_played2 = Counter()
        gear_destroyed1 = Counter()
        gear_destroyed2 = Counter()
        gear_trashed1 = Counter()
        gear_trashed2 = Counter()
        gear_on_table1 = Counter()
        gear_on_table2 = Counter()
        for game_num in range(num_games):
            collect_log_flag = (num_games == 1)
            try:
                res = simulate_game(strategy1, strategy2, log=False, custom_hp=(hp1, hp2), collect_log=collect_log_flag, user_strategy1=user_strategy1, user_strategy2=user_strategy2, log_options=log_options)
            except Exception as e:
                print('=== ERROR in simulate_game ===')
                print(traceback.format_exc())
                return jsonify({'result': 'error', 'error': str(e), 'traceback': traceback.format_exc()}), 500
            turns_list.append(res['turns'])
            p1 = res['player1']
            p2 = res['player2']
            # HP
            hp1_end.append(p1.health)
            hp2_end.append(p2.health)
            # Damage (разница стартового и финального HP обоих игроков)
            total_damage += (hp1 - p1.health) + (hp2 - p2.health)
            total_damage1 += (hp2 - p2.health)  # сколько P1 нанёс P2
            total_damage2 += (hp1 - p1.health)  # сколько P2 нанёс P1
            # Value карт (по частоте трэша и встречаемости в колоде)
            for idx, pl in enumerate([p1, p2]):
                strat = [user_strategy1, user_strategy2][idx]
                for c in getattr(pl, 'deck', []) + getattr(pl, 'discard', []) + getattr(pl, 'hand', []):
                    if c.name not in starter_names and is_card_allowed(c.name, enabled_cards, strat):
                        card_value[c.name] = card_value.get(c.name, 0) + 1
            # Трэш
            for idx, pl in enumerate([p1, p2]):
                strat = [user_strategy1, user_strategy2][idx]
                for c in getattr(pl, 'trash_pile', []):
                    if c.name not in starter_names and is_card_allowed(c.name, enabled_cards, strat):
                        trash_counter[c.name] = trash_counter.get(c.name, 0) + 1
            # Gear absorb (если у карты есть поле absorbed_damage)
            for pl in [p1, p2]:
                for c in getattr(pl, 'deck', []) + getattr(pl, 'discard', []) + getattr(pl, 'hand', []):
                    if hasattr(c, 'is_gear') and getattr(c, 'is_gear', False):
                        val = getattr(c, 'absorbed_damage', 0)
                        gear_absorb[c.name] = gear_absorb.get(c.name, 0) + val
            # Победы
            if res['winner'] == 'P1':
                win1 += 1
                winner = p1
                loser = p2
                opp = p2
                # Победа через яд
                if getattr(p2, 'poison', 0) >= 20:
                    poison_win1 += 1
            elif res['winner'] == 'P2':
                win2 += 1
                winner = p2
                loser = p1
                opp = p1
                # Победа через яд
                if getattr(p1, 'poison', 0) >= 20:
                    poison_win2 += 1
            else:
                opp = None
                winner = None
                loser = None
            # Аналитика по победителю/проигравшему
            if winner and loser:
                for idx, pl in enumerate([winner, loser]):
                    strat = [user_strategy1, user_strategy2][[winner, loser].index(pl)]
                    counter = winner_card_counter if pl is winner else loser_card_counter
                    for c in getattr(pl, 'deck', []) + getattr(pl, 'discard', []) + getattr(pl, 'hand', []):
                        if c.name not in starter_names and is_card_allowed(c.name, enabled_cards, strat):
                            counter[c.name] = counter.get(c.name, 0) + 1
                # HP и яд победителя
                winner_hp.append(winner.health)
                winner_poison.append(getattr(winner, 'poison', 0))
                hp_diff.append(winner.health - loser.health)
            # Новые метрики по gear и spy
            gear_saved_damage_p1 = getattr(res, 'gear_saved_damage_p1', getattr(res['player1'], 'gear_saved_damage', 0))
            gear_saved_damage_p2 = getattr(res, 'gear_saved_damage_p2', getattr(res['player2'], 'gear_saved_damage', 0))
            spy_discarded_p1 = getattr(res, 'spy_discarded_p1', getattr(res['player1'], 'spy_discarded', 0))
            spy_discarded_p2 = getattr(res, 'spy_discarded_p2', getattr(res['player2'], 'spy_discarded', 0))
            gear_destroyed_p1 = getattr(res, 'gear_destroyed_p1', getattr(res['player1'], 'gear_destroyed', 0))
            gear_destroyed_p2 = getattr(res, 'gear_destroyed_p2', getattr(res['player2'], 'gear_destroyed', 0))
            # Суммируем по всем играм
            if 'gear_saved_damage' not in locals():
                gear_saved_damage = [0, 0]
                spy_discarded = [0, 0]
                gear_destroyed = [0, 0]
            gear_saved_damage[0] += gear_saved_damage_p1
            gear_saved_damage[1] += gear_saved_damage_p2
            spy_discarded[0] += spy_discarded_p1
            spy_discarded[1] += spy_discarded_p2
            gear_destroyed[0] += gear_destroyed_p1
            gear_destroyed[1] += gear_destroyed_p2
            # Новые метрики по эффектам и трэшу
            sum_damage_dealt1 += res.get('damage_dealt1', 0)
            sum_damage_dealt2 += res.get('damage_dealt2', 0)
            sum_poison_dealt1 += res.get('poison_dealt1', 0)
            sum_poison_dealt2 += res.get('poison_dealt2', 0)
            sum_bleed_dealt1 += res.get('bleed_dealt1', 0)
            sum_bleed_dealt2 += res.get('bleed_dealt2', 0)
            sum_heal_received1 += res.get('heal_received1', 0)
            sum_heal_received2 += res.get('heal_received2', 0)
            sum_poison_heal_received1 += res.get('poison_heal_received1', 0)
            sum_poison_heal_received2 += res.get('poison_heal_received2', 0)
            sum_bleed_heal_received1 += res.get('bleed_heal_received1', 0)
            sum_bleed_heal_received2 += res.get('bleed_heal_received2', 0)
            sum_trash1 += res.get('trash1', 0)
            sum_trash2 += res.get('trash2', 0)
            sum_trash_this1 += res.get('trash_this1', 0)
            sum_trash_this2 += res.get('trash_this2', 0)
            # Gear-статистика
            gs1 = res.get('gear_stats1', {})
            gs2 = res.get('gear_stats2', {})
            gear_played1.update(gs1.get('played', {}))
            gear_played2.update(gs2.get('played', {}))
            gear_destroyed1.update(gs1.get('destroyed', {}))
            gear_destroyed2.update(gs2.get('destroyed', {}))
            gear_trashed1.update(gs1.get('trashed', {}))
            gear_trashed2.update(gs2.get('trashed', {}))
            gear_on_table1.update(gs1.get('on_table', {}))
            gear_on_table2.update(gs2.get('on_table', {}))
            # Яд и кровоток
            poison1_end.append(getattr(p1, 'poison', 0))
            poison2_end.append(getattr(p2, 'poison', 0))
            bleed1_end.append(getattr(p1, 'bleed', 0))
            bleed2_end.append(getattr(p2, 'bleed', 0))
        avg_turns = sum(turns_list) / len(turns_list) if turns_list else 0
        avg_hp1 = sum(hp1_end) / len(hp1_end) if hp1_end else 0
        avg_hp2 = sum(hp2_end) / len(hp2_end) if hp2_end else 0
        avg_poison1 = sum(poison1_end) / len(poison1_end) if poison1_end else 0
        avg_poison2 = sum(poison2_end) / len(poison2_end) if poison2_end else 0
        avg_bleed1 = sum(bleed1_end) / len(bleed1_end) if bleed1_end else 0
        avg_bleed2 = sum(bleed2_end) / len(bleed2_end) if bleed2_end else 0
        avg_damage = total_damage / num_games if num_games else 0
        # Топ трэш
        trashed_cards = sorted(trash_counter.items(), key=lambda x: -x[1])
        # Топ value
        top_cards = sorted(card_value.items(), key=lambda x: -x[1])[:10]
        # Топ gear (только реально сыгравшие gear)
        top_gear = sorted(gear_absorb.items(), key=lambda x: -x[1])
        # Новая статистика по Priestess
        def priestess_stats(player):
            bought = sum(1 for c in getattr(player, 'deck', []) + getattr(player, 'discard', []) + getattr(player, 'hand', []) if getattr(c, 'name', '').lower() == 'priestess')
            trashed = sum(1 for c in getattr(player, 'trash_pile', []) if getattr(c, 'name', '').lower() == 'priestess')
            as_gear = sum(1 for c in getattr(player, 'gear', []) if getattr(c, 'name', '').lower() == 'priestess')
            absorbed = sum(getattr(c, 'absorbed_damage', 0) for c in getattr(player, 'deck', []) + getattr(player, 'discard', []) + getattr(player, 'hand', []) + getattr(player, 'gear', []) if getattr(c, 'name', '').lower() == 'priestess')
            return {'bought': bought, 'trashed': trashed, 'as_gear': as_gear, 'absorbed': absorbed}
        priestess_stats1 = priestess_stats(p1)
        priestess_stats2 = priestess_stats(p2)
        # Новые метрики
        top_winner_cards = sorted(winner_card_counter.items(), key=lambda x: -x[1])[:10]
        top_loser_cards = sorted(loser_card_counter.items(), key=lambda x: -x[1])[:10]
        avg_winner_hp = sum(winner_hp) / len(winner_hp) if winner_hp else 0
        avg_winner_poison = sum(winner_poison) / len(winner_poison) if winner_poison else 0
        avg_hp_diff = sum(hp_diff) / len(hp_diff) if hp_diff else 0
        # gear_saved_damage теперь средний по игрокам
        gear_saved_damage_avg = [round(x/num_games, 2) for x in gear_saved_damage]
        # Проценты побед
        P1_win_percent = round(100 * win1 / num_games, 2) if num_games else 0
        P2_win_percent = round(100 * win2 / num_games, 2) if num_games else 0
        results = {
            'result': 'ok',
            'stats': {
                'P1_win': win1,
                'P2_win': win2,
                'P1_win_percent': P1_win_percent,
                'P2_win_percent': P2_win_percent,
                'avg_turns': round(avg_turns, 2),
                'avg_hp1': round(avg_hp1, 2),
                'avg_hp2': round(avg_hp2, 2),
                'avg_poison1': round(avg_poison1, 2),
                'avg_poison2': round(avg_poison2, 2),
                'avg_bleed1': round(avg_bleed1, 2),
                'avg_bleed2': round(avg_bleed2, 2),
                'avg_damage': round(avg_damage, 2),
                'avg_damage1': round(total_damage1 / num_games, 2),
                'avg_damage2': round(total_damage2 / num_games, 2),
                'gear_saved_damage': gear_saved_damage_avg, # средний урон, поглощённый gear у каждого игрока
                'gear_saved_damage_total': gear_saved_damage, # суммарно по gear для каждого игрока
                'trashed_cards': trashed_cards,
                'top_cards': top_cards,
                'gear_absorbed': top_gear,
                'params': {
                    'strategy1': strategy1,
                    'strategy2': strategy2,
                    'hp1': hp1,
                    'hp2': hp2,
                    'num_games': num_games,
                    'enabled_cards': list(enabled_cards)
                },
                # Новые метрики:
                'top_winner_cards': top_winner_cards,
                'top_loser_cards': top_loser_cards,
                'avg_winner_hp': round(avg_winner_hp, 2),
                'avg_winner_poison': round(avg_winner_poison, 2),
                'avg_hp_diff': round(avg_hp_diff, 2),
                'spy_discarded': spy_discarded,
                'gear_destroyed': gear_destroyed,
                'poison_win1': poison_win1,
                'poison_win2': poison_win2,
                # Новая статистика по Priestess:
                'priestess_stats1': priestess_stats1,
                'priestess_stats2': priestess_stats2,
                # Новые средние метрики по эффектам и трэшу:
                'avg_damage_dealt1': round(sum_damage_dealt1 / num_games, 2),
                'avg_damage_dealt2': round(sum_damage_dealt2 / num_games, 2),
                'avg_poison_dealt1': round(sum_poison_dealt1 / num_games, 2),
                'avg_poison_dealt2': round(sum_poison_dealt2 / num_games, 2),
                'avg_bleed_dealt1': round(sum_bleed_dealt1 / num_games, 2),
                'avg_bleed_dealt2': round(sum_bleed_dealt2 / num_games, 2),
                'avg_heal_received1': round(sum_heal_received1 / num_games, 2),
                'avg_heal_received2': round(sum_heal_received2 / num_games, 2),
                'avg_poison_heal_received1': round(sum_poison_heal_received1 / num_games, 2),
                'avg_poison_heal_received2': round(sum_poison_heal_received2 / num_games, 2),
                'avg_bleed_heal_received1': round(sum_bleed_heal_received1 / num_games, 2),
                'avg_bleed_heal_received2': round(sum_bleed_heal_received2 / num_games, 2),
                'avg_trash1': round(sum_trash1 / num_games, 2),
                'avg_trash2': round(sum_trash2 / num_games, 2),
                'avg_trash_this1': round(sum_trash_this1 / num_games, 2),
                'avg_trash_this2': round(sum_trash_this2 / num_games, 2),
                # Gear-статистика по всем играм (по именам карт):
                'gear_played1': dict(gear_played1),
                'gear_played2': dict(gear_played2),
                'gear_destroyed1': dict(gear_destroyed1),
                'gear_destroyed2': dict(gear_destroyed2),
                'gear_trashed1': dict(gear_trashed1),
                'gear_trashed2': dict(gear_trashed2),
                'gear_on_table1': dict(gear_on_table1),
                'gear_on_table2': dict(gear_on_table2),
            }
        }
        # --- Универсальная функция сериализации карты ---
        def card_to_dict(card):
            if isinstance(card, dict):
                return card
            return {
                'name': getattr(card, 'name', None),
                'effect1': getattr(card, 'effect1', None),
                'effect2': getattr(card, 'effect2', None),
                'cost': getattr(card, 'cost', None),
                'color': getattr(card, 'color', None),
                'is_gear': getattr(card, 'is_gear', False),
                'absorbed_damage': getattr(card, 'absorbed_damage', 0),
                'defends': getattr(card, 'defends', False),
                'defense': getattr(card, 'defense', 0),
                'hp': getattr(card, 'hp', 0),
                'raw': {},
                # --- Добавляю поле icon для gear ---
                'icon': '🛡️' if getattr(card, 'is_gear', False) else ''
            }
        # --- Универсальная функция сериализации игрока ---
        def player_to_dict(player):
            return {
                'health': getattr(player, 'health', None),
                'poison': getattr(player, 'poison', None),
                'bleed': getattr(player, 'bleed', None),
                'deck': [card_to_dict(c) for c in getattr(player, 'deck', [])],
                'hand': [card_to_dict(c) for c in getattr(player, 'hand', [])],
                'discard': [card_to_dict(c) for c in getattr(player, 'discard', [])],
                'gear': [card_to_dict(c) for c in getattr(player, 'gear', [])],
                'trash_pile': [card_to_dict(c) for c in getattr(player, 'trash_pile', [])],
            }
        # detailed_log только для одной игры
        if num_games == 1:
            # Patch detailed_log to convert Card objects to dicts
            def patch_log(log):
                if isinstance(log, list):
                    return [patch_log(x) for x in log]
                if isinstance(log, dict):
                    newd = {}
                    for k, v in log.items():
                        # Сериализуем все списки карт
                        if k in ('hand', 'deck', 'discard', 'gear', 'trash_pile', 'played', 'bought') and isinstance(v, list):
                            newd[k] = [card_to_dict(c) if hasattr(c, 'name') else c for c in v]
                        # Сериализуем списки шагов покупок и состояний рынка
                        elif k in ('buy_steps', 'market_states') and isinstance(v, list):
                            newd[k] = [patch_log(x) for x in v]
                        # Сериализуем эффекты
                        elif k == 'effects' and isinstance(v, list):
                            newd[k] = []
                            for eff in v:
                                if isinstance(eff, dict) and 'card' in eff and hasattr(eff['card'], 'name'):
                                    eff2 = eff.copy()
                                    eff2['card'] = getattr(eff['card'], 'name', str(eff['card']))
                                    newd[k].append(eff2)
                                else:
                                    newd[k].append(eff)
                        else:
                            newd[k] = patch_log(v)
                    return newd
                return log
            # Patch player1/player2 if present
            if 'player1' in res and hasattr(res['player1'], 'deck'):
                results['player1'] = player_to_dict(res['player1'])
            if 'player2' in res and hasattr(res['player2'], 'deck'):
                results['player2'] = player_to_dict(res['player2'])
            # Patch detailed_log if present
            if 'detailed_log' in res:
                results['detailed_log'] = patch_log(res['detailed_log'])
            # --- ДОБАВЛЯЮ: в конце каждого хода (end_status) сохраняю подробную информацию ---
            def enrich_end_status(action, player):
                # Добавить подробную инфу о руке, колоде, сбросе, трэше
                action['end_status']['hand_count'] = len(getattr(player, 'hand', []))
                action['end_status']['deck_count'] = len(getattr(player, 'deck', []))
                action['end_status']['discard_count'] = len(getattr(player, 'discard', []))
                action['end_status']['trash_count'] = len(getattr(player, 'trash_pile', []))
                action['end_status']['hand_cards'] = [getattr(c, 'name', None) for c in getattr(player, 'hand', [])]
                action['end_status']['deck_cards'] = [getattr(c, 'name', None) for c in getattr(player, 'deck', [])]
                action['end_status']['discard_cards'] = [getattr(c, 'name', None) for c in getattr(player, 'discard', [])]
                action['end_status']['trash_cards'] = [getattr(c, 'name', None) for c in getattr(player, 'trash_pile', [])]
            # enrich detailed_log
            if 'detailed_log' in results and isinstance(results['detailed_log'], list):
                for turn in results['detailed_log']:
                    for action in turn.get('actions', []):
                        # played: сохраняем повторы (каждый экземпляр)
                        if 'played' in action and isinstance(action['played'], list):
                            action['played'] = [card_to_dict(c) if hasattr(c, 'name') else c for c in action['played']]
                        # enrich end_status
                        if 'end_status' in action:
                            pl = None
                            if action.get('player') == 'P1' and 'player1' in results:
                                pl = res['player1']
                            elif action.get('player') == 'P2' and 'player2' in results:
                                pl = res['player2']
                            if pl:
                                enrich_end_status(action, pl)
                results['detailed_log'] = patch_log(results['detailed_log'])
        # Сохраняем в файл
        # timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        # with open(f'simulation_result_{timestamp}.json', 'w', encoding='utf-8') as file:
        #     json.dump(results, file, ensure_ascii=False, indent=2)
        return jsonify(results)
    except Exception as e:
        print('=== ERROR in /api/simulate ===')
        print(traceback.format_exc())
        return jsonify({'result': 'error', 'error': str(e), 'traceback': traceback.format_exc()}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True) 