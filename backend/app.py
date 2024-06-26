from flask import Flask, jsonify, request
from flask_cors import CORS
from urllib.parse import urlparse, unquote
from pathlib import Path
import psycopg2
import os
from dotenv import load_dotenv
import random

from ml_scripts.card_fetcher import CardsContext
from ml_scripts.converter import MLConverter
from ml_scripts.card_embedder import CardEmbedder
from ml_scripts.card_parser import CardParser
import joblib

ml_db = CardsContext()
ml_converter = MLConverter()
ml_card_embedder = CardEmbedder()
ml_card_parser = CardParser()

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [
    "https://cardcognition.com", 
    "http://cardcognition.com", 
    "https://www.cardcognition.com", 
    "http://www.cardcognition.com",
    "https://api.cardcognition.com",
    "http://api.cardcognition.com",
    "https://www.api.cardcognition.com",
    "http://www.api.cardcognition.com",
    "http://localhost:3000",
]}})

# Database Configuration
db_config = {
    'name': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST'),
    'port': os.getenv('DB_PORT')
}

# Connect to the database
conn = psycopg2.connect(
    dbname=db_config['name'],
    user=db_config['user'],
    password=db_config['password'],
    host=db_config['host'],
    port=db_config['port']
)
cur = conn.cursor()

longest_commander_name = 31

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "Success!"}), 200

@app.route('/dbinfo', methods=['GET'])
def get_db_info():
    cur.execute("""
        SELECT 
            AVG(c.synergy_score) as avg_synergy_score,
            (SELECT AVG(avg_synergy_score)
            FROM (
                SELECT AVG(c.synergy_score) as avg_synergy_score
                FROM edhrec_cards c
                GROUP BY c.commander_id
            ) AS commander_avg_synergies) as avg_commander_synergy_score,
        COUNT(DISTINCT cmd.id) as commander_count,
        COUNT(*) as card_commander_pairs_count,
        COUNT(DISTINCT c.card_name) as unique_card_count
        FROM edhrec_cards c
        JOIN edhrec_commanders cmd ON c.commander_id = cmd.id
    """)
    data = cur.fetchone()
    avg_synergy_score, avg_commander_synergy_score, commander_count, card_commander_pairs_count, unique_card_count = data
    return jsonify({"avg_synergy_score": avg_synergy_score, "avg_commander_synergy_score": avg_commander_synergy_score, "commander_count": commander_count, "card_commander_pairs_count": card_commander_pairs_count, "unique_card_count": unique_card_count}), 200


@app.route('/<commander_name>/info', methods=['GET'])
def get_commander_info(commander_name):

    if len(commander_name) > longest_commander_name:
        return jsonify({"error": "Commander name too long."}), 400

    cur.execute("""
        SELECT cmd.name, cmd.scryfall_id,
        (SELECT AVG(c.synergy_score) FROM edhrec_cards c WHERE c.commander_id = cmd.id) as avg_synergy_score,
        (SELECT COUNT(*) FROM edhrec_cards c WHERE c.commander_id = cmd.id) as card_count
        FROM edhrec_commanders cmd
        WHERE cmd.name = %s
    """, (commander_name,))

    data = cur.fetchone()
    if not data:
        return jsonify({"error": "Commander not found."}), 404
    name, scryfall_id, avg_synergy_score, card_count = data

    cur.execute("""
        SELECT cmd2.card_name, cmd2.name, cmd2.scryfall_id, COUNT(DISTINCT c2.card_id), COUNT(DISTINCT c2.card_id) * 100.0 / %s as overlap_percentage
        FROM edhrec_cards c1
        JOIN edhrec_commanders cmd1 ON c1.commander_id = cmd1.id
        JOIN edhrec_cards c2 ON c1.card_id = c2.card_id
        JOIN edhrec_commanders cmd2 ON c2.commander_id = cmd2.id
        WHERE cmd1.name = %s AND cmd1.id != cmd2.id
        GROUP BY cmd2.card_name, cmd2.name, cmd2.scryfall_id
        ORDER BY overlap_percentage DESC
        LIMIT 5
    """, (card_count, commander_name))


    similar_commanders = [
        {
            "card_name": card_name, 
            "name": name, 
            "scryfall_id": scryfall_id, 
            "overlap_count": overlap_count, 
            "overlap_percentage": round(overlap_percentage, 2)
        } for card_name, name, scryfall_id, overlap_count, overlap_percentage in cur.fetchall()
    ]

    return jsonify({
        "name": name,
        "scryfall_id": scryfall_id,
        "avg_synergy_score": avg_synergy_score,
        "card_count": card_count,
        "similar_commanders": similar_commanders
    }), 200

@app.route('/random-commander', methods=['GET'])
def get_random_commander():
    # Get number of commanders
    cur.execute("""
        SELECT COUNT(*) FROM edhrec_commanders
    """)
    count = cur.fetchone()[0]

    index = random.randint(1, count)
    # Get random commander by id
    cur.execute("""
        SELECT cmd.card_name
        FROM edhrec_commanders cmd
        LIMIT 1 OFFSET %s
    """, (index,))
    random_commander = cur.fetchone()[0]
        
    return jsonify({"commander_name": random_commander}), 200

@app.route('/<commander_name>/suggestions/<count>', methods=['GET'])
def get_suggestions(commander_name, count):
    # Get the suggestions for the commander
    try:
        int(count)
    except ValueError:
        # Probably using range, so count would look like "range/100/200"
        # Redirect to range endpoint
        return get_suggestions_range(commander_name, count.split('/')[1], count.split('/')[2])

    if len(commander_name) > longest_commander_name:
        return jsonify({"error": "Commander name too long."}), 400

    if int(count) > 100:
        count = 100

    cur.execute("""
    SELECT sc.card_name, c.synergy_score, sc.scryfall_id
    FROM edhrec_cards c
    JOIN edhrec_commanders cmd ON c.commander_id = cmd.id
    JOIN scryfall_cards sc ON c.card_id = sc.id
    WHERE cmd.name = %s
    ORDER BY c.synergy_score DESC
    LIMIT %s
    """, (commander_name, count))

    data = cur.fetchall()
    suggestions = [{'name': name, 'score': score, 'scryfall_id': scryfall_id} for name, score, scryfall_id in data]
    
    if not suggestions:
        return jsonify({"error": "No suggestions found for this commander."}), 404
    return jsonify({"suggestions": suggestions, "count": count}), 200

@app.route('/<commander_name>/suggestions/range/<start>/<end>', methods=['GET'])
def get_suggestions_range(commander_name, start, end):
    # Get the suggestions for the commander
    try:
        int(start)
        int(end)
    except ValueError:
        return jsonify({"error": "Start and end must be integers."}), 400

    # Longest commander name is 31 characters (Asmoranomardicadaistinaculdacar)
    if len(commander_name) > longest_commander_name:
        return jsonify({"error": "Commander name too long."}), 400

    if int(start) < 0:
        start = 0
    if int(end) > int(start) + 100:
        end = start + 100

    cur.execute("""
    SELECT sc.card_name, c.synergy_score, sc.scryfall_id
    FROM edhrec_cards c
    JOIN edhrec_commanders cmd ON c.commander_id = cmd.id
    JOIN scryfall_cards sc ON c.card_id = sc.id
    WHERE cmd.name = %s
    ORDER BY c.synergy_score DESC
    LIMIT %s
    OFFSET %s
    """, (commander_name, end, start))
    data = cur.fetchall()
    suggestions = [{'name': name, 'score': score, 'scryfall_id': scryfall_id} for name, score, scryfall_id in data]
    if not suggestions:
        return jsonify({"error": "No suggestions found for this commander."}), 404
    return jsonify({"suggestions": suggestions, "start": start, "end": end}), 200

@app.route('/<commander_name>/reductions/<count>', methods=['GET'])
def get_reductions(commander_name, count):
    try:
        count = int(count)
    except ValueError:
        return jsonify({"error": "Count must be an integer."}), 400
    
    if len(commander_name) > longest_commander_name:
        return jsonify({"error": "Commander name too long."}), 400
    
    if count > 100:
        count = 100

    cur.execute("""
        SELECT sc.card_name, c.percentage, c.synergy_score, sc.scryfall_id
        FROM edhrec_cards c
        JOIN edhrec_commanders cmd ON c.commander_id = cmd.id
        JOIN scryfall_cards sc ON c.card_id = sc.id
        WHERE cmd.name = %s AND c.synergy_score < 0.8
        ORDER BY c.percentage ASC, c.synergy_score ASC
        LIMIT %s
    """, (commander_name, count))
    data = cur.fetchall()

    reductions = [{'name': name, 'percentage': percentage, 'score': score, 'scryfall_id': scryfall_id} for name, percentage, score, scryfall_id in data]

    if not reductions:
        return jsonify({"error": "No reductions found for this commander."}), 404
    return jsonify({"reductions": reductions, "count": count}), 200

@app.route('/cards/<card_name>', methods=['GET'])
def get_card(card_name):

    # Shessra%2C%20Death%27s%20Whisper => shessra, death's whisper
    card_name = unquote(card_name.lower())

    cur.execute("""
        SELECT sc.card_name, sc.mana_cost, sc.cmc, sc.type_line, sc.oracle_text, sc.colors, sc.color_identity, sc.commander_legal, sc.set_code, sc.rarity, sc.prices, sc.edhrec_rank
        FROM scryfall_cards sc
        WHERE LOWER(sc.card_name) = %s
    """, (card_name,))

    data = list(cur.fetchone())
    if not data:
        return jsonify({"error": "Card not found."}), 404

    return jsonify({
        "name": data[0],
        "mana_cost": data[1],
        "cmc": data[2],
        "type_line": data[3],
        "oracle_text": data[4],
        "colors": data[5],
        "color_identity": data[6],
        "commander_legal": data[7],
        "set_code": data[8],
        "rarity": data[9],
        "prices": data[10],
        "edhrec_rank": data[11]
    }), 200

@app.route('/analyze/<raw_commander_name>', methods=['POST'])
def analyze(raw_commander_name):
    print(raw_commander_name)
    content_type = request.headers.get('Content-Type')
    print(request)
    print(request.headers)
    print(content_type)
    if (content_type != 'application/json'):
        return 'Content-Type not supported!'
    json = request.json

    print(json)
    
    cur.execute("""
        SELECT cmd.card_name
        FROM edhrec_commanders cmd
        WHERE cmd.name = %s
    """, (raw_commander_name,))

    commander_name = cur.fetchone()[0]

    print(cur.fetchone())

    if not commander_name:
        return jsonify({"error": "Commander not found."}), 404
    
    print(commander_name)
    req_cards = json
    cards = ml_db.get_related_cards_from_commander_name(commander_name)
    cards = [card for card in cards if card['card_name'] in req_cards]

    for card_name in req_cards:
        if card_name not in [card['card_name'] for card in cards]:
            card = ml_db.get_card_by_name(card_name)
            if card:
                cards.append(card)

    embeddings = ml_card_embedder.embed_and_parse_cards(cards, testing=True)
    formatted_name = ml_converter.sanitize_filename(commander_name)
    commander_model = joblib.load(f"ml_scripts/cmd_models/{formatted_name}.joblib")

    scores = {card['card_name']: 0 for card in cards}
    parsed_cards = [ml_card_parser.parse_card(card) for card in cards]
    
    for i, card in enumerate(cards):
        try:
            if i % 1000 == 0:
                print(f"Scoring {i} of {len(cards)}")
            card_embedding = embeddings[i]
            if card_embedding is None:
                continue
            score = round(commander_model.predict([card_embedding])[0], 2)
            scores[card['card_name']] = score
        except Exception as e:
            continue
    
    return jsonify({"scores": scores, "parsed_cards": parsed_cards}), 200
    

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)