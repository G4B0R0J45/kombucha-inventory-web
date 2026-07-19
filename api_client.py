"""El Fermentario — API client (Unit IV: consuming an API with `requests`).

The server side of this repo (app/) *offers* the inventory over HTTP; this
script sits on the other side of the counter and *orders* from it, the way
any external program would. Together they close the Unit IV circle: we
built the waiter, and now we are also the customer.

Run the server in one terminal:

    uvicorn app.main:app --reload

then this client in another:

    python api_client.py
"""

import requests

# Connection details live in one place so pointing this client at a hosted
# server later (roadmap step 5) becomes a one-line change — the same reason
# storage.py centralizes the data-file paths.
BASE_URL = "http://localhost:8000"
TIMEOUT_SECONDS = 5  # a network call must never be allowed to wait forever


def get_json(path, params=None):
    """GET `path` and return the decoded JSON body.

    Guard clause: anything but 200 raises immediately, so every caller can
    trust that whatever this returns is real data — no caller needs its
    own status check for reads.
    """
    response = requests.get(BASE_URL + path, params=params,
                            timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise RuntimeError(
            f"GET {path} answered {response.status_code}: {response.text}")
    return response.json()


def show_inventory():
    """Print current stock per flavor — the client-side twin of the CLI's
    inventory screen, except the numbers now arrive over HTTP."""
    data = get_json("/inventory")
    print("Inventory:")
    for row in data["inventory"]:
        print(f"  {row['flavor_name']:<15} {row['total']:>4} bottles "
              f"(min {row['minimum_stock']:>3})  [{row['stock_status']}]")
    if data["inconsistencies"]:
        print(f"  ({len(data['inconsistencies'])} history warnings — "
              "see GET /warnings)")


def summarize_exits(movements):
    """Total bottles that left in `movements`.

    Unit IV functional tools on real data: filter() keeps only the exits,
    map() extracts each quantity, and each takes a lambda — a one-line,
    anonymous function — as its rule. (The CSV stores quantities as text,
    hence the int() cast.)
    """
    exits = filter(lambda m: m["movement_type"] == "exit", movements)
    quantities = map(lambda m: int(m["bottle_quantity"]), exits)
    return sum(quantities)


def register_movement(staff_name, flavor_name, movement_type,
                      bottle_quantity, lot_number="", note=""):
    """POST one movement and translate each HTTP outcome for a human.

    Defaults make the two optional fields truly optional, and calling this
    with keyword arguments (see main) keeps every call self-documenting.
    """
    body = {
        "staff_name": staff_name,
        "flavor_name": flavor_name,
        "movement_type": movement_type,
        "bottle_quantity": bottle_quantity,
        "lot_number": lot_number,
        "note": note,
    }
    # Our own API needs no key, but public ones usually do — and a header
    # like {"X-API-Key": "..."} is exactly where such a credential travels.
    headers = {"Accept": "application/json"}
    response = requests.post(BASE_URL + "/movements", json=body,
                             headers=headers, timeout=TIMEOUT_SECONDS)

    # Branch on the status code, never on the prose: the codes are the part
    # of the contract that programs are meant to read.
    if response.status_code == 201:        # created
        r = response.json()
        print(f"Registered: {r['flavor_name']} -> stock {r['current_stock']} "
              f"[{r['stock_status']}]  lots: {r['lot_display'] or '-'}")
    elif response.status_code == 409:      # understood, but impossible now
        print("Conflict:", response.json()["detail"]["message"])
    elif response.status_code == 422:      # the request itself is invalid
        print("Invalid request:", response.json()["detail"])
    else:
        print(f"Unexpected {response.status_code}: {response.text}")


def main():
    print(f"El Fermentario client -> {BASE_URL}\n")
    print("Server says:", get_json("/health")["status"], "\n")

    show_inventory()

    recent = get_json("/movements", params={"limit": 10})["movements"]
    print(f"\nLast {len(recent)} movements: "
          f"{summarize_exits(recent)} bottles out.")

    # Uncomment to register a real exit over HTTP. It writes to the real
    # movements.csv — run it once, then find the new row in the history:
    # register_movement("Carolina", "Jamaica", "exit", bottle_quantity=3,
    #                   note="sold via api_client demo")


if __name__ == "__main__":
    # Import-safe guard: `python api_client.py` runs the demo; importing
    # this module (say, from a test) runs nothing.
    try:
        main()
    except requests.exceptions.ConnectionError:
        print("Could not connect — is the server up?  "
              "uvicorn app.main:app --reload")
    except requests.exceptions.Timeout:
        print(f"No answer within {TIMEOUT_SECONDS}s — "
              "server overloaded or frozen.")
