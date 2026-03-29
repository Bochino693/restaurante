# seu_app/context_processors.py
from .models import Pedidos

def global_context(request):
    # Supondo que você queira contar pedidos criados hoje ou com status pendente
    count = Pedidos.objects.filter(total__gt=0).count() # Ajuste seu filtro aqui
    return {
        'pedidos_pendentes_count': count
    }