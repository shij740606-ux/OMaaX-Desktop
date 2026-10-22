"""Local journal candidates; the model ranks the bundled scope descriptions."""
class MatchError(Exception):
    pass
async def match(abstract, journals):
    # There are five curated scopes: retain all, rather than pretending lexical scores are vectors.
    return [{"journal": j, "similarity": None} for j in journals], False
