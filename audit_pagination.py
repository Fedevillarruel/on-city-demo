import requests
from scraper_oncity import get_leaf_categories, get_category_total, fetch_page

def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    print("Fetching categories...")
    categories = get_leaf_categories(session)
    print(f"Found {len(categories)} leaf categories.")
    
    audited = 0
    total_overlap_count = 0
    partial_overlap_count = 0
    problematic_cases = []
    healthy_cases = []
    
    for cat in categories:
        path = cat['path']
        name = cat['name']
        
        try:
            total = get_category_total(session, path)
        except Exception as e:
            print(f"Error getting total for {name}: {e}")
            continue
            
        if total >= 120:
            audited += 1
            print(f"Auditing {name} (total={total})...")
            
            try:
                p1 = fetch_page(session, 0, 49, path)
                p2 = fetch_page(session, 50, 99, path)
            except Exception as e:
                print(f"Error fetching pages for {name}: {e}")
                continue
            
            ids1 = [item.get('productId') for item in p1 if item.get('productId')]
            ids2 = [item.get('productId') for item in p2 if item.get('productId')]
            
            set1 = set(ids1)
            set2 = set(ids2)
            overlap = set1.intersection(set2)
            overlap_len = len(overlap)
            
            if overlap_len > 0:
                if overlap_len == len(ids1) and overlap_len == len(ids2):
                    total_overlap_count += 1
                else:
                    partial_overlap_count += 1
                
                if len(problematic_cases) < 15:
                    problematic_cases.append({
                        'name': name,
                        'path': path,
                        'total': total,
                        'len_p1': len(ids1),
                        'len_p2': len(ids2),
                        'overlap': overlap_len
                    })
            else:
                if len(healthy_cases) < 5:
                    unique_count = len(set1.union(set2))
                    healthy_cases.append({
                        'name': name,
                        'unique': unique_count
                    })

    print("\n--- AUDIT REPORT ---")
    print(f"Total categories audited (total >= 120): {audited}")
    print(f"Categories with total overlap: {total_overlap_count}")
    print(f"Categories with partial overlap (>0): {partial_overlap_count}")
    
    if problematic_cases:
        print("\nProblematic Cases (up to 15):")
        print(f"{'Name':<30} | {'Total':<6} | {'P1':<4} | {'P2':<4} | {'Overlap':<7}")
        for case in problematic_cases:
            print(f"{case['name'][:30]:<30} | {case['total']:<6} | {case['len_p1']:<4} | {case['len_p2']:<4} | {case['overlap']:<7}")
            
    if healthy_cases:
        print("\nSample of Healthy Categories (Unique products p1+p2):")
        for hc in healthy_cases:
            print(f"{hc['name']}: {hc['unique']}")

if __name__ == "__main__":
    main()
