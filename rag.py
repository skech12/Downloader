from dotenv import load_dotenv
from cc0_content import CC0Client

#.env example CC0_CONTENT_API_KEY=exampleKey
load_dotenv()

client = CC0Client()
res = client.search("history of rome")
print(res["chunks"])
