import http.client
import json

def get_proactive_events_token(client_id, client_secret):
    conn = http.client.HTTPSConnection("api.amazon.com")
    payload = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}&scope=alexa::proactive_events"
    headers = {'Content-Type': "application/x-www-form-urlencoded;charset=UTF-8"}
    conn.request("POST", "/auth/o2/token", payload, headers)
    res = conn.getresponse()
    data = res.read()
    return json.loads(data.decode("utf-8"))

# Usage
client_id = "amzn1.application-oa2-client.5569e8ec73464d7eb3e0dc2796475d97"
client_secret = "amzn1.oa2-cs.v1.40cba8b5cf11772979bbbc37f3fcc66e9ebdf867da029d7f77b9977d3c0aab72"
result = get_proactive_events_token(client_id, client_secret)
print(f"Access Token: {result.get('access_token')}")