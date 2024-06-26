from pathlib import Path
import psycopg2
import os
import json
from dotenv import load_dotenv
try: 
    from ml_scripts.converter import MLConverter
except ModuleNotFoundError:
    from converter import MLConverter

class CardsContext:
    def __init__(self):
        self.converter = MLConverter()
        self.BASE_DIR = Path(__file__).resolve().parent
        load_dotenv(os.path.join(self.BASE_DIR, '..\.env'))

        # Connect to the database
        self.conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        self.cur = self.conn.cursor()

        try:
            with open('config.json', 'r') as f:
                self.config = json.load(f)
        except:
            with open('ml_scripts/config.json', 'r') as f:
                self.config = json.load(f)

    # DB Columns and example data but in JSON format for readability:
    # edhrec_commanders table
    #   "id"	"name"	                    "scryfall_id"	                        "card_name"	            "card_id"
    #   7	    "abaddon-the-despoiler"	    "c9f1fc97-00c0-492b-a4a3-b179afc2f95d"	"Abaddon the Despoiler"	20423
    #
    # edhrec_cards table
    #   "id"	"commander_id"	"percentage"	"num_decks"	"synergy_score"	"card_id"
    #   173993	748	            24	            2392	    12.0	        27164
    #
    # scryfall_cards table
    # "id"	"scryfall_id"	                        "card_name"	    "mana_cost"	"cmc"	"type_line"	"oracle_text"
    # 22901	"55552a2b-1861-4235-a60d-ccabb4839d54"	"Aura Graft"	"{1}{U}"	2	    "Instant"	"Gain control of target Aura that's attached to a permanent. Attach it to ansub permanent it can enchant."	"U"	"U"	true	"10e"	"uncommon"	"{""usd"": ""0.18"", ""usd_foil"": ""0.62"", ""usd_etched"": null, ""eur"": ""0.09"", ""eur_foil"": ""0.39"", ""tix"": ""0.02""}"	17200
    # (Cont.)
    # "colors" "color_identity" "commander_legal"	"set_code"	"rarity"	"edhrec_rank"   "prices"
    # "U"	    "U"	            true	            "10e"	    "uncommon"	17200           "{""usd"": ""0.18"", ""usd_foil"": ""0.62"", ""usd_etched"": null, ""eur"": ""0.09"", ""eur_foil"": ""0.39"", ""tix"": ""0.02""}"

    def fetch_list_of_dicts(self, cursor)-> list:
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_all_cards(self) -> list:
        self.cur.execute("""
            SELECT * FROM scryfall_cards
            WHERE commander_legal = true
            ORDER BY id ASC
        """)
        return self.fetch_list_of_dicts(self.cur)

    def get_commander_sc_id_by_id(self, card_id:int) -> int:
        self.cur.execute("""
            SELECT sc.id FROM scryfall_cards sc
            INNER JOIN edhrec_commanders ON sc.id = edhrec_commanders.card_id
            AND commander_legal = true
            ORDER BY id ASC
        """, (card_id,))
        return self.cur.fetchone()[0]
    
    def get_all_card_types_and_sub_types(self):
        return self.get_card_types_and_sub_types(self.get_all_cards())
    
    def get_card_types_and_sub_types(self, card_list:list):
        super_types = set()
        card_types = set()
        sub_types = set()
        for row in card_list:
            type_dict = self.converter.process_type_line(row['type_line'])
            super_types.update(set(type_dict.get('super_types', [])))
            card_types.update(set(type_dict.get('card_types', [])))
            sub_types.update(set(type_dict.get('sub_types', [])))
        return list(super_types), list(card_types), list(sub_types)

    def get_card_by_id(self, card_id:int) -> dict:
        self.cur.execute("""
            SELECT * FROM scryfall_cards
            WHERE id = %s
            AND commander_legal = true
            ORDER BY id ASC
        """, (card_id,))
        return self.fetch_list_of_dicts(self.cur)[0]
    
    def get_card_ids_of_commanders(self) -> list:
        self.cur.execute("""
            SELECT ec.card_name, ec.card_id FROM edhrec_commanders ec
            INNER JOIN scryfall_cards sc ON ec.card_id = sc.id
            WHERE ec.card_name IS NOT NULL
            AND ec.id IS NOT NULL
            AND ec.card_id IS NOT NULL
            AND sc.commander_legal = true
            ORDER BY card_id ASC
        """)
        return [{'card_name': row[0], 'card_id': row[1]} for row in self.cur.fetchall()]
    
    def get_cards(self) -> list:
        self.cur.execute("""
            SELECT * FROM scryfall_cards
            WHERE commander_legal = true
            ORDER BY id ASC
        """)
        return self.fetch_list_of_dicts(self.cur)
    
    def get_cmd_pct_relations(self) -> list:
        self.cur.execute("""
            SELECT cmd.card_id AS cmd_card_id, ec.card_id AS ec_card_id, percentage, synergy_score
            FROM edhrec_cards ec
            INNER JOIN edhrec_commanders cmd ON ec.commander_id = cmd.id
            INNER JOIN scryfall_cards sc1 ON ec.card_id = sc1.id
            INNER JOIN scryfall_cards sc2 ON cmd.card_id = sc2.id
            WHERE sc1.commander_legal = true AND sc2.commander_legal = true
            AND ec.num_decks > %s
            GROUP BY cmd.card_id, ec.card_id, percentage, synergy_score
            ORDER BY cmd.card_id ASC
        """, (self.config['min_num_decks'],))
        return [{'commander_id': row[0], 'card_id': row[1], 'percentage': row[2], 'synergy_score': row[3]} for row in self.cur.fetchall()]

    def get_related_cards_from_commander_name(self, commander_name:str) -> list:
        self.cur.execute("""
            SELECT ec.card_id, ec.percentage, ec.num_decks, ec.synergy_score, sc.*
            FROM edhrec_cards ec
            INNER JOIN scryfall_cards sc ON ec.card_id = sc.id
            WHERE ec.commander_id = (
                SELECT id FROM edhrec_commanders
                WHERE card_name = %s
            )
            AND sc.commander_legal = true
            ORDER BY ec.card_id ASC;
        """, (commander_name,))
        return self.fetch_list_of_dicts(self.cur)

    def get_commanders(self) -> list:
        self.cur.execute("""
            SELECT * FROM scryfall_cards sc
            WHERE id IN (
                SELECT card_id FROM edhrec_commanders ec
            ) AND commander_legal = true
            ORDER BY id ASC
        """)
        return self.fetch_list_of_dicts(self.cur)
    
    def get_commander_by_id(self, commander_id:int) -> dict:
        self.cur.execute("""
            SELECT * FROM edhrec_commanders
            WHERE id = %s
            ORDER BY id ASC
        """, (commander_id,))
        return self.fetch_list_of_dicts(self.cur)[0]
    
    def get_commander_synergies_by_id(self, commander_id:int) -> list:
        self.cur.execute("""
            SELECT ec.card_id, ec.synergy_score
            FROM edhrec_cards ec
            INNER JOIN scryfall_cards sc ON ec.card_id = sc.id
            WHERE commander_id = %s
            AND sc.commander_legal = true
            ORDER BY ec.card_id ASC;
        """, (commander_id,))
        return self.fetch_list_of_dicts(self.cur)
    
    def get_cmd_pct_relations_by_id(self, card_id:str) -> list:
        self.cur.execute("""
            SELECT cmd.card_id AS cmd_card_id, ec.card_id AS ec_card_id, percentage, synergy_score
            FROM edhrec_cards ec
            INNER JOIN edhrec_commanders cmd ON ec.commander_id = cmd.id
            INNER JOIN scryfall_cards sc1 ON ec.card_id = sc1.id
            INNER JOIN scryfall_cards sc2 ON cmd.card_id = sc2.id
            WHERE sc1.commander_legal = true AND sc2.commander_legal = true
            AND cmd.card_id = %s
            GROUP BY cmd.card_id, ec.card_id, percentage, synergy_score
        """, (card_id,))
        return [{'commander_id': row[0], 'card_id': row[1], 'percentage': row[2], 'synergy_score': row[3]} for row in self.cur.fetchall()]

    def get_commander_frequencies_by_id(self, commander_id:int) -> list:
        self.cur.execute("""
            SELECT ec.card_id, ec.percentage
            FROM edhrec_cards ec
            INNER JOIN scryfall_cards sc ON ec.card_id = sc.id
            WHERE ec.commander_id = %s
            AND sc.commander_legal = true
            ORDER BY ec.card_id ASC;
        """, (commander_id,))
        return self.fetch_list_of_dicts(self.cur)

    def get_cmd_id_from_sc_id(self, scryfall_id:int) -> int:
        self.cur.execute("""
            SELECT cmd.id AS commander_id
            FROM scryfall_cards sc
            INNER JOIN edhrec_commanders cmd ON sc.card_name = cmd.card_name
            WHERE sc.id = %s
            AND sc.commander_legal = true
        """, (scryfall_id,))
        return self.cur.fetchone()[0]

    def get_card_synergies_by_id(self, card_id:int) -> list:
        self.cur.execute("""
            SELECT commander_id, synergy_score FROM edhrec_cards
            WHERE card_id = %s
            AND commander_legal = true
            ORDER BY commander_id ASC
        """, (card_id,))
        return self.fetch_list_of_dicts(self.cur)
    
    def get_card_batch_by_id(self, card_ids:list) -> list:
        self.cur.execute("""
            SELECT * FROM scryfall_cards
            WHERE id = ANY(%s)
        """, (card_ids,))
        return self.fetch_list_of_dicts(self.cur)
    
    def get_commander_batch_by_id(self, commander_ids:list) -> list:
        self.cur.execute("""
            SELECT [sc].* FROM scryfall_cards sc
            INNER JOIN edhrec_commanders ec
            ON sc.id = ec.card_id
            WHERE sc.id = ANY(%s)
        """, (commander_ids,))
        return self.fetch_list_of_dicts(self.cur)

    def get_card_by_name(self, card_name:str) -> dict:
        self.cur.execute("""
            SELECT * FROM scryfall_cards
            WHERE card_name = %s
            AND commander_legal = true
            ORDER BY id ASC
        """, (card_name,))
        if self.cur.rowcount == 0:
            return None
        return self.fetch_list_of_dicts(self.cur)[0]
    
    def get_id_by_name(self, card_name:str) -> int:
        self.cur.execute("""
            SELECT id FROM scryfall_cards
            WHERE card_name = %s
        """, (card_name,))
        return self.fetch_list_of_dicts(self.cur)
        