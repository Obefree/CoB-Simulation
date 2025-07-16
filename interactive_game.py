import random
from app import parse_main_cards, parse_starters
import simulator as sim

# Setup card data
sim.MAIN_CARDS = parse_main_cards()
sim.STARTER_CARDS = parse_starters()

# Utilities
def count_blessing(cards):
    total = 0
    for card in cards:
        for field in ['effect1', 'effect2', 'effect1text', 'effect2text']:
            val = getattr(card, field, None)
            if val:
                for eff in sim.parse_effects_from_string(val):
                    if isinstance(eff, (list, tuple)) and eff[0] == 'blessing':
                        total += sim.safe_int(eff[1])
    return total


def print_state(player):
    print(f"{player.name}: HP={player.health} Poison={player.poison} Bleed={player.bleed}")


def play_card(card, player, opponent):
    sim.apply_card_effects(card, player, opponent, log=True, trash_list=[])
    # Move card to discard if not trashed
    for zone in [player.hand, player.discard, player.deck, player.gear]:
        if card in zone:
            zone.remove(card)
    if card not in player.trash_pile:
        player.discard.append(card)


def human_turn(player, opponent, market):
    player.start_turn_statuses()
    total_bless = count_blessing(player.hand)
    spent = 0
    print_state(player)
    while True:
        print("Hand:")
        for i, c in enumerate(player.hand):
            print(f"  {i+1}. {c.name} ({c.effect1} / {c.effect2})")
        cmd = input("Play card number or 'end': ").strip()
        if cmd.lower() == 'end' or cmd == '':
            break
        try:
            idx = int(cmd) - 1
            card = player.hand[idx]
        except (ValueError, IndexError):
            print("Invalid choice")
            continue
        play_card(card, player, opponent)
        print_state(player)
        if player.health <= 0 or player.poison >= 20:
            return
    # Buying phase
    while True:
        affordable = [c for c in market.trade_row if c.cost <= (total_bless - spent)]
        if not affordable:
            break
        print("Market:")
        for i, c in enumerate(market.trade_row):
            flag = '*' if c in affordable else ' '
            print(f"  {i+1}. {c.name} (Cost {c.cost}){flag}")
        choice = input(f"Buy card number (Blessing left {total_bless - spent}) or 'end': ").strip()
        if choice.lower() == 'end' or choice == '':
            break
        try:
            mi = int(choice) - 1
            if market.trade_row[mi].cost > (total_bless - spent):
                print("Not enough blessing")
                continue
            bought = market.buy_card(mi, player)
            spent += bought.cost
            print(f"Bought {bought.name}")
        except (ValueError, IndexError):
            print("Invalid choice")
    player.end_turn()


def bot_turn(player, opponent, market, pattern='red'):
    player.start_turn_statuses()
    total_bless = count_blessing(player.hand)
    hand_queue = list(player.hand)
    while hand_queue:
        card = hand_queue.pop(0)
        play_card(card, player, opponent)
        # Newly drawn cards are in player.hand
        for c in player.hand:
            if c not in hand_queue:
                hand_queue.append(c)
    spent = 0
    while True:
        idx = sim.buy_strategy(player, market, total_bless, spent, pattern)
        if idx is None:
            break
        card = market.buy_card(idx, player)
        spent += card.cost
        if spent >= total_bless:
            break
    player.end_turn()


def main():
    human = sim.Player('You')
    bot = sim.Player('Bot')
    market = sim.TradeMarket(sim.MAIN_CARDS)
    human.market = market
    bot.market = market
    turn = 1
    while True:
        print(f"\n=== Turn {turn} ===")
        human.current_turn = turn
        bot.current_turn = turn
        human_turn(human, bot, market)
        if human.health <= 0 or human.poison >= 20:
            print("You lost!")
            break
        if bot.health <= 0 or bot.poison >= 20:
            print("You win!")
            break
        bot_turn(bot, human, market)
        if human.health <= 0 or human.poison >= 20:
            print("You lost!")
            break
        if bot.health <= 0 or bot.poison >= 20:
            print("You win!")
            break
        turn += 1

if __name__ == '__main__':
    main()
