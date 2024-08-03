import json
import os

# Define paths (ensure these match your project structure)
# Assuming test_runner.py is in the root of the internships-bot project
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LISTINGS_JSON_PATH = os.path.join(ROOT_DIR, 'Summer2025-Internships', '.github', 'scripts', 'listings.json')
PREVIOUS_DATA_JSON_PATH = os.path.join(ROOT_DIR, 'previous_data.json')

# Ensure the directory for listings.json exists
LISTINGS_DIR = os.path.dirname(LISTINGS_JSON_PATH)
if not os.path.exists(LISTINGS_DIR):
    os.makedirs(LISTINGS_DIR)
    print(f"Created directory: {LISTINGS_DIR}")

# --- Data Scenarios ---

scenario_1_listings = [
    {
        "id": "sim001",
        "title": "Steady Active Job",
        "company_name": "SimCorp",
        "active": True,
        "is_visible": True,
        "url": "http://example.com/steady",
        "locations": ["SimCity"],
        "season": "TestSeason",
        "sponsorship": "Unknown",
        "date_updated": 1700000000,
        "date_posted": 1700000000,
        "company_url": "",
        "source": "test-script"
    },
    {
        "id": "sim002",
        "title": "Job To Be Deactivated",
        "company_name": "SimCorp",
        "active": True,
        "is_visible": True,
        "url": "http://example.com/deactivate_me",
        "locations": ["SimCity"],
        "season": "TestSeason",
        "sponsorship": "Unknown",
        "date_updated": 1700000001,
        "date_posted": 1700000001,
        "company_url": "",
        "source": "test-script"
    }
]

scenario_2_listings = [
    {
        "id": "sim001",
        "title": "Steady Active Job",
        "company_name": "SimCorp",
        "active": True,
        "is_visible": True,
        "url": "http://example.com/steady",
        "locations": ["SimCity"],
        "season": "TestSeason",
        "sponsorship": "Unknown",
        "date_updated": 1700000000,
        "date_posted": 1700000000,
        "company_url": "",
        "source": "test-script"
    },
    {
        "id": "sim002",
        "title": "Job To Be Deactivated",
        "company_name": "SimCorp",
        "active": False, # Changed
        "is_visible": True,
        "url": "http://example.com/deactivate_me",
        "locations": ["SimCity"],
        "season": "TestSeason",
        "sponsorship": "Unknown",
        "date_updated": 1700000001,
        "date_posted": 1700000001,
        "company_url": "",
        "source": "test-script"
    },
    {
        "id": "sim003",
        "title": "Brand New Job",
        "company_name": "SimCorp",
        "active": True,
        "is_visible": True,
        "url": "http://example.com/brand_new",
        "locations": ["SimCity"],
        "season": "TestSeason",
        "sponsorship": "Unknown",
        "date_updated": 1700000002,
        "date_posted": 1700000002,
        "company_url": "",
        "source": "test-script"
    }
]

def write_json_file(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"Successfully wrote to {file_path}")

def setup_scenario_1():
    print("\n--- Setting up Scenario 1: Initial Listings ---")
    write_json_file(LISTINGS_JSON_PATH, scenario_1_listings)
    if os.path.exists(PREVIOUS_DATA_JSON_PATH):
        os.remove(PREVIOUS_DATA_JSON_PATH)
        print(f"Removed existing {PREVIOUS_DATA_JSON_PATH}")
    print("Scenario 1 setup complete.")
    print(f"ACTION: Now, please run 'python mainbot.py'.")
    print("Expected output: 'Steady Active Job (sim001)' and 'Job To Be Deactivated (sim002)' announced as new.")
    print("After mainbot.py finishes, it should create previous_data.json.")

def setup_scenario_2():
    print("\n--- Setting up Scenario 2: Changes and New Listing ---")
    if not os.path.exists(PREVIOUS_DATA_JSON_PATH):
        print(f"ERROR: {PREVIOUS_DATA_JSON_PATH} does not exist. Please run Scenario 1 and mainbot.py first.")
        return
    write_json_file(LISTINGS_JSON_PATH, scenario_2_listings)
    print("Scenario 2 setup complete.")
    print(f"ACTION: Now, please run 'python mainbot.py' again.")
    print("Expected output: 'Job To Be Deactivated (sim002)' announced as inactive, 'Brand New Job (sim003)' announced as new.")

def main():
    while True:
        print("\nSelect an action:")
        print("1. Set up Scenario 1 (Initial state)")
        print("2. Set up Scenario 2 (Changes and new listing)")
        print("3. Exit")
        choice = input("Enter your choice (1-3): ")

        if choice == '1':
            setup_scenario_1()
        elif choice == '2':
            setup_scenario_2()
        elif choice == '3':
            print("Exiting test runner.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main() 