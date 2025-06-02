from django.db import models

class UBRWealthQuintiles(models.IntegerChoices):
    POOREST = 1, "Poorest"
    POORER = 2, "Poorer"
    POOR = 3, "Poor"
    BETTER = 4, "Better"
    RICH = 5, "Rich"

class UBRRegion(models.IntegerChoices):
    NORTHERN = 1, "Northern"
    CENTRAL = 2, "Central"
    SOUTHERN = 3, "Southern"
