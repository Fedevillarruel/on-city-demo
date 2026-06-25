import requests

def test_endpoint(name, url_base, session):
    print(f"\n--- Testing: {name} ---")
    
    # Page 1: 0-49
    sep = "&" if "?" in url_base else "?"
    url1 = f"https://www.oncity.com{url_base}{sep}_from=0&_to=49"
    resp1 = session.get(url1)
    
    # Page 2: 50-99
    url2 = f"https://www.oncity.com{url_base}{sep}_from=50&_to=99"
    resp2 = session.get(url2)
    
    print(f"P1 (0-49) Status: {resp1.status_code}, Resources Header: {resp1.headers.get('resources')}")
    print(f"P2 (50-99) Status: {resp2.status_code}, Resources Header: {resp2.headers.get('resources')}")
    
    ids1 = []
    if resp1.status_code in [200, 206]:
        try:
            data1 = resp1.json()
            ids1 = [str(p.get('productId')) for p in data1]
        except Exception as e:
            print(f"Error parsing P1 JSON: {e}")

    ids2 = []
    if resp2.status_code in [200, 206]:
        try:
            data2 = resp2.json()
            ids2 = [str(p.get('productId')) for p in data2]
        except Exception as e:
            print(f"Error parsing P2 JSON: {e}")
    
    print(f"P1 len: {len(ids1)}, P2 len: {len(ids2)}")
    
    overlap = set(ids1).intersection(set(ids2))
    print(f"Overlap productIds count: {len(overlap)}")
    if overlap:
        print(f"Overlap IDs: {list(overlap)[:5]}...")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

# 1. Path endpoint (Category 113 Piletas)
test_endpoint("1) Path endpoint (Piletas)", "/api/catalog_system/pub/products/search/aire-libre/piletas-y-accesorios/piletas", session)

# 2. Endpoint with fq by id (C:113)
test_endpoint("2) FQ ID (C:113)", "/api/catalog_system/pub/products/search/?fq=C:113", session)

# 3. Global endpoint
test_endpoint("3) Global", "/api/catalog_system/pub/products/search/", session)
