from cc0_content import CC0Client

client = CC0Client()  # auto login/token/api-key bootstrap
res = client.search("history of rome")
print(res["chunks"])
