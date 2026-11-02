import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

from backend.api import server
from backend.assistant_knowledge import retrieve_feature_knowledge


class AgriNewsTests(unittest.TestCase):
    @patch.object(server.requests, "get")
    def test_agri_news_parses_recent_articles_and_omits_stale_or_invalid_items(self, get):
        recent_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=1))
        stale_date = format_datetime(datetime.now(timezone.utc) - timedelta(days=10))
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = f"""<?xml version="1.0"?>
            <rss><channel>
              <item>
                <title>New MSP policy for farmers</title>
                <link>https://news.example/article</link>
                <pubDate>{recent_date}</pubDate>
                <source>News Publisher</source>
                <description>&lt;p&gt;Latest farm update&lt;/p&gt;</description>
              </item>
              <item>
                <title>Old crop update</title>
                <link>https://news.example/old</link>
                <pubDate>{stale_date}</pubDate>
              </item>
              <item>
                <title>Missing publication date</title>
                <link>https://news.example/invalid</link>
                <pubDate>not a date</pubDate>
              </item>
            </channel></rss>""".encode()
        get.return_value = response

        result = server.get_agri_news(category="policy")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["news"][0]["category"], "policy")
        self.assertEqual(result["news"][0]["source"], "News Publisher")
        self.assertEqual(result["news"][0]["snippet"], "Latest farm update")
        self.assertEqual(result["news"][0]["url"], "https://news.example/article")
        self.assertIn("when:7d", get.call_args.kwargs["params"]["q"])

    @patch.object(server.requests, "get", side_effect=server.requests.Timeout)
    def test_agri_news_reports_feed_failure_without_static_fallback(self, _get):
        result = server.get_agri_news()

        self.assertEqual(result["status"], "error")
        self.assertIn("temporarily unavailable", result["message"])
        self.assertNotIn("news", result)


class WebSearchTests(unittest.TestCase):
    @patch.object(server.requests, "get")
    def test_web_search_uses_google_news_when_duckduckgo_has_no_results(self, get):
        duckduckgo = Mock()
        duckduckgo.raise_for_status.return_value = None
        duckduckgo.text = "<html><body>No result entries</body></html>"
        google_news = Mock()
        google_news.raise_for_status.return_value = None
        google_news.content = b"""<?xml version="1.0"?>
            <rss><channel><item>
              <title>New farmer subsidy update</title>
              <link>https://news.google.com/rss/articles/example</link>
              <source>PIB</source>
              <pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
            </item></channel></rss>"""
        get.side_effect = [duckduckgo, google_news]

        results = server._search_web("India farmer subsidies", limit=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "New farmer subsidy update")
        self.assertIn("PIB", results[0]["snippet"])
        self.assertEqual(
            results[0]["url"],
            "https://news.google.com/rss/articles/example"
        )
        self.assertEqual(get.call_count, 2)


class ChatAssistantTests(unittest.TestCase):
    def test_market_request_requires_a_crop(self):
        response = server.chat({"message": "What is the mandi price today?"})

        self.assertIn("Which crop", response["reply"])

    @patch.object(server, "_search_web", return_value=[
        {
            "title": "Current government farmer support update",
            "snippet": "Published by Press Information Bureau on Nov 2, 2026",
            "url": "https://news.google.com/rss/articles/subsidy-update"
        }
    ])
    def test_best_subsidies_question_includes_live_search_results(self, search_web):
        response = server.chat({"message": "tell me best subsidies for me"})

        self.assertEqual(response["action_link"], "subsidies.html")
        self.assertEqual(response["action_text"], "Open Farmer Subsidies")
        self.assertIn("PM-KUSUM", response["reply"])
        self.assertIn("not live-verified", response["reply"])
        self.assertIn("I searched online just now", response["reply"])
        self.assertIn("[Current government farmer support update]", response["reply"])
        self.assertIn("https://news.google.com/rss/articles/subsidy-update", response["reply"])
        self.assertIn("your state, crop", response["reply"])
        search_web.assert_called_once()

    @patch.object(server, "_search_web", return_value=[])
    def test_subsidy_question_discloses_when_online_search_has_no_results(self, search_web):
        response = server.chat({"message": "tell me good subsidies"})

        self.assertIn("couldn't retrieve useful online results", response["reply"])
        self.assertNotIn("recent search results", response["reply"])
        self.assertEqual(response["action_link"], "subsidies.html")
        search_web.assert_called_once()

    @patch.object(server, "get_market_prices", return_value={
        "source": "Agmarknet National Crop Database",
        "data": [{"commodity": "Maize", "modal_price": 24.0}]
    })
    def test_maize_prize_typo_is_routed_to_market_prices(self, get_prices):
        response = server.chat({"message": "tell me maize prize"})

        get_prices.assert_called_once()
        self.assertIn("current Maize prices", response["reply"])
        self.assertNotIn("relevant app entry", response["reply"])

    @patch.object(server, "_gps_market_area", return_value="Gwalior, Madhya Pradesh")
    @patch.object(server, "get_market_prices", return_value={
        "source": "Data.gov.in Agmarknet Live API",
        "data": [
            {
                "commodity": "Maize",
                "market": "Indore Mandi",
                "district": "Indore",
                "state": "Madhya Pradesh",
                "min_price": 18.0,
                "max_price": 22.0,
                "modal_price": 20.0,
                "arrival_date": "2026-11-02"
            },
            {
                "commodity": "Maize",
                "market": "Delhi Mandi",
                "district": "Delhi",
                "state": "Delhi",
                "min_price": 25.0,
                "max_price": 30.0,
                "modal_price": 28.0,
                "arrival_date": "2026-11-02"
            }
        ]
    })
    def test_maize_chat_uses_state_matches_when_gps_district_has_no_maize(self, _get_prices, _gps_area):
        response = server.chat({
            "message": "tell me maize prices",
            "lat": 26.2451,
            "lon": 78.3141
        })

        self.assertIn("state match", response["reply"])
        self.assertIn("Indore Mandi", response["reply"])
        self.assertIn("₹18.00–₹22.00/kg", response["reply"])
        self.assertNotIn("Delhi Mandi", response["reply"])
        self.assertIn("Exact nearest-mandi distance is unavailable", response["reply"])

    @patch.object(server, "get_market_prices")
    def test_nearby_mandi_question_requires_gps_or_a_place(self, get_prices):
        response = server.chat({"message": "tomato price near me"})

        get_prices.assert_not_called()
        self.assertIn("Allow GPS", response["reply"])

    @patch.object(server, "get_market_prices")
    @patch.object(server, "mistral_chat", side_effect=RuntimeError("no test API key"))
    def test_fertilizer_rate_is_not_mistaken_for_a_crop_market_price(self, _mistral_chat, get_prices):
        response = server.chat({
            "message": "What fertilizer rate should I use?",
            "crop": "Tomato",
            "farm_context": {"nitrogen": 35, "phosphorus": 42, "potassium": 50}
        })

        get_prices.assert_not_called()
        self.assertIn("NPK Advisor", response["reply"])

    @patch.object(server, "get_market_prices")
    def test_sample_market_data_is_not_reported_as_a_current_price(self, get_prices):
        get_prices.return_value = {
            "source": "Agmarknet National Crop Database",
            "data": [{"commodity": "Tomato", "modal_price": 28.5}]
        }

        response = server.chat({"message": "What is the current tomato price?"})

        self.assertNotIn("₹28.5", response["reply"])
        self.assertIn("town or district", response["reply"])

    @patch.object(server, "get_market_prices")
    def test_live_market_result_is_grounded_and_location_filtered(self, get_prices):
        get_prices.return_value = {
            "source": "Data.gov.in Agmarknet Live API",
            "data": [
                {
                    "commodity": "Tomato",
                    "market": "Ludhiana Mandi",
                    "district": "Ludhiana",
                    "state": "Punjab",
                    "min_price": 22.0,
                    "max_price": 34.0,
                    "modal_price": 28.5,
                    "arrival_date": "2026-11-02"
                },
                {
                    "commodity": "Tomato",
                    "market": "Azadpur Mandi",
                    "district": "North West",
                    "state": "Delhi",
                    "min_price": 25.0,
                    "max_price": 38.0,
                    "modal_price": 31.0,
                    "arrival_date": "2026-11-02"
                }
            ]
        }

        response = server.chat({"message": "tomato price in Ludhiana"})

        self.assertIn("₹22.00–₹34.00/kg", response["reply"])
        self.assertIn("Ludhiana Mandi", response["reply"])
        self.assertNotIn("Azadpur", response["reply"])
        self.assertIn("Data.gov.in Agmarknet Live API", response["reply"])

    @patch.object(server, "_gps_market_area", return_value="Ludhiana, Punjab")
    @patch.object(server, "get_market_prices")
    def test_gps_chat_uses_district_market_records_without_claiming_exact_distance(self, get_prices, _gps_area):
        get_prices.return_value = {
            "source": "Data.gov.in Agmarknet Live API",
            "data": [
                {
                    "commodity": "Tomato",
                    "market": "Ludhiana Mandi",
                    "district": "Ludhiana",
                    "state": "Punjab",
                    "min_price": 22.0,
                    "max_price": 34.0,
                    "modal_price": 28.5,
                    "arrival_date": "2026-11-02"
                },
                {
                    "commodity": "Tomato",
                    "market": "Azadpur Mandi",
                    "district": "North West",
                    "state": "Delhi",
                    "min_price": 25.0,
                    "max_price": 38.0,
                    "modal_price": 31.0,
                    "arrival_date": "2026-11-02"
                }
            ]
        }

        response = server.chat({
            "message": "tomato market price near me",
            "lat": 30.9,
            "lon": 75.8
        })

        self.assertIn("district match", response["reply"])
        self.assertIn("Ludhiana Mandi", response["reply"])
        self.assertNotIn("Azadpur", response["reply"])
        self.assertIn("Exact nearest-mandi distance is unavailable", response["reply"])

    @patch.object(server, "_search_web", return_value=[
        {
            "title": "Tomato prices in Ludhiana",
            "snippet": "Latest tomato mandi prices reported for Ludhiana.",
            "url": "https://example.com/ludhiana-tomato"
        },
        {
            "title": "Tomato prices in Delhi",
            "snippet": "Latest tomato mandi prices reported for Delhi.",
            "url": "https://example.com/delhi-tomato"
        }
    ])
    @patch.object(server, "get_market_prices", return_value={
        "source": "Data.gov.in Agmarknet Live API",
        "data": []
    })
    def test_missing_local_live_price_uses_only_crop_and_location_matched_web_results(
        self, _get_prices, _search_web
    ):
        response = server.chat({"message": "tomato price in Ludhiana"})

        self.assertIn("unverified", response["reply"])
        self.assertIn("Ludhiana", response["reply"])
        self.assertIn("https://example.com/ludhiana-tomato", response["reply"])
        self.assertNotIn("Delhi", response["reply"])

    @patch.object(server, "_gps_market_area", return_value="Ludhiana, Punjab")
    @patch.object(server.requests, "get")
    @patch.object(server, "load_api_keys", return_value={"DATA_GOV_API_KEY": "test-key"})
    def test_market_api_filters_live_records_to_gps_district(self, _keys, get, _gps_area):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "records": [
                {
                    "commodity": "Tomato", "market": "Ludhiana Mandi",
                    "district": "Ludhiana", "state": "Punjab",
                    "min_price": "2200", "max_price": "3400", "modal_price": "2850"
                },
                {
                    "commodity": "Tomato", "market": "Azadpur Mandi",
                    "district": "North West", "state": "Delhi",
                    "min_price": "2500", "max_price": "3800", "modal_price": "3100"
                },
                {
                    "commodity": "Tomato", "market": "Amritsar Mandi",
                    "district": "Amritsar", "state": "Punjab",
                    "min_price": "2100", "max_price": "3300", "modal_price": "2700"
                }
            ]
        }
        get.return_value = response

        result = server.get_market_prices(lat=30.9, lon=75.8, commodity="Tomato")

        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["data"][0]["market"], "Ludhiana Mandi")
        self.assertEqual(result["data"][0]["location_match"], "District match")
        self.assertEqual(result["data"][1]["market"], "Amritsar Mandi")
        self.assertEqual(result["data"][1]["location_match"], "State match")
        self.assertNotIn("distance_km", result["data"][0])
        self.assertIn("exact nearest-market distances are unavailable", result["location_note"])
        request_params = get.call_args.kwargs["params"]
        self.assertEqual(request_params["filters[state]"], "Punjab")
        self.assertEqual(request_params["filters[commodity]"], "Tomato")
        self.assertEqual(request_params["limit"], 1000)

    @patch.object(server, "_gps_market_area", return_value="Ludhiana, Punjab")
    @patch.object(server.requests, "get")
    @patch.object(server, "load_api_keys", return_value={"DATA_GOV_API_KEY": "test-key"})
    def test_market_api_paginates_state_records_to_keep_crop_variety(self, _keys, get, _gps_area):
        district_record = {
            "commodity": "Tomato", "market": "Ludhiana Mandi",
            "district": "Ludhiana", "state": "Punjab",
            "min_price": "2200", "max_price": "3400", "modal_price": "2850"
        }
        state_record = {
            "commodity": "Potato", "market": "Amritsar Mandi",
            "district": "Amritsar", "state": "Punjab",
            "min_price": "1800", "max_price": "2600", "modal_price": "2200"
        }
        first_page = Mock()
        first_page.status_code = 200
        first_page.json.return_value = {
            "records": [district_record] + [
                {**state_record, "commodity": f"Crop {index}"}
                for index in range(999)
            ],
            "total": "1001"
        }
        second_page = Mock()
        second_page.status_code = 200
        second_page.json.return_value = {
            "records": [{**state_record, "commodity": "Potato"}],
            "total": "1001"
        }
        get.side_effect = [first_page, second_page]

        result = server.get_market_prices(lat=30.9, lon=75.8)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], 0)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 1000)
        self.assertEqual(len(result["data"]), 1001)
        self.assertEqual(result["data"][0]["location_match"], "District match")
        self.assertEqual(result["data"][-1]["location_match"], "State match")

    @patch.object(server, "_gps_market_area", return_value="Ludhiana, Punjab")
    @patch.object(server.requests, "get")
    @patch.object(server, "load_api_keys", return_value={"DATA_GOV_API_KEY": "test-key"})
    def test_market_api_falls_back_to_same_state_when_district_has_no_prices(self, _keys, get, _gps_area):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "records": [
                {
                    "commodity": "Tomato", "market": "Jalandhar Mandi",
                    "district": "Jalandhar", "state": "Punjab",
                    "min_price": "2200", "max_price": "3400", "modal_price": "2850"
                },
                {
                    "commodity": "Tomato", "market": "Azadpur Mandi",
                    "district": "North West", "state": "Delhi",
                    "min_price": "2500", "max_price": "3800", "modal_price": "3100"
                }
            ]
        }
        get.return_value = response

        result = server.get_market_prices(lat=30.9, lon=75.8, commodity="Tomato")

        self.assertEqual([item["market"] for item in result["data"]], ["Jalandhar Mandi"])
        self.assertEqual(result["data"][0]["location_match"], "State match")

    def test_feature_discovery_lists_available_tools(self):
        with patch.object(server, "mistral_chat", side_effect=RuntimeError("no test API key")):
            response = server.chat({"message": "What features can you help with?"})

        self.assertIn("Market Details", response["reply"])
        self.assertIn("Weather & Alerts", response["reply"])
        self.assertIn("action_link", response)

    def test_retrieval_covers_every_feature_shown_in_the_dashboard(self):
        examples = {
            "soil sensor dashboard": "index.html",
            "mandi crop price": "market.html",
            "nearby fertilizer supplier": "farm_support.html",
            "emergency SOS voice report fire incident nearby farmers": "sos.html",
            "calculate harvest yield per acre": "yield_calculator.html",
            "track plant height growth": "plant_growth.html",
            "farmer government subsidies": "subsidies.html",
            "soil moisture water stress irrigation": "water_stress.html",
            "inspect uploaded farm image": "farm_inspector.html",
            "farm input costs and expenses": "input_cost.html",
            "soil test nitrogen phosphorus potassium fertilizer NPK": "npk_advisor.html",
            "farmer education learning resources": "farmer_education.html",
            "agricultural news and trends": "agri_news.html",
            "agent orchestration candidate plans": "dashboard2.html",
            "weather alerts forecast rain": "weather.html",
            "nearby bank farm finance loan": "finance.html",
        }

        for question, expected_route in examples.items():
            with self.subTest(question=question):
                matches = retrieve_feature_knowledge(question, limit=1)
                self.assertTrue(matches)
                self.assertEqual(matches[0]["route"], expected_route)

    @patch.object(server, "mistral_chat", side_effect=RuntimeError("no test API key"))
    def test_retrieved_feature_knowledge_is_used_without_the_llm(self, _mistral_chat):
        response = server.chat({"message": "How do I use the NPK soil advisor?"})

        self.assertIn("matching app information", response["reply"])
        self.assertIn("NPK Advisor", response["reply"])
        self.assertEqual(response["action_link"], "npk_advisor.html")

    def test_current_weather_without_location_requests_a_location(self):
        response = server.chat({"message": "What is the weather today?"})

        self.assertIn("Share a town or district", response["reply"])

    @patch.object(server.requests, "get")
    def test_weather_question_uses_named_location_and_labels_source(self, get):
        geocoding = Mock()
        geocoding.json.return_value = {
            "results": [{
                "latitude": 31.63,
                "longitude": 74.87,
                "name": "Amritsar",
                "admin1": "Punjab",
                "country": "India"
            }]
        }
        forecast = Mock()
        forecast.json.return_value = {
            "current": {
                "temperature_2m": 27,
                "relative_humidity_2m": 60,
                "apparent_temperature": 28,
                "weather_code": 1,
                "wind_speed_10m": 8
            },
            "daily": {
                "time": ["2026-11-02"],
                "temperature_2m_min": [19],
                "temperature_2m_max": [29],
                "precipitation_sum": [0]
            },
            "timezone": "Asia/Kolkata"
        }
        get.side_effect = [geocoding, forecast]
        with patch.object(server, "mistral_chat", return_value="Current temperature is 27°C. Source: Open-Meteo."):
            response = server.chat({"message": "What is the weather today in Amritsar?"})

        self.assertIn("Open-Meteo", response["reply"])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
