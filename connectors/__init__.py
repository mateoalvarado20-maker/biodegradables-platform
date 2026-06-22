"""Implementaciones concretas de los conectores (este paquete SÍ nombra vendors).

Cada adaptador envuelve por delegación un cliente existente de la raíz
(contifico_client, hubspot_client, graph_mail) SIN modificarlo, y satisface una
interfaz de `core.connectors.base`. El cliente subyacente es inyectable para poder
testear sin red. Los bots en producción todavía NO usan estos adaptadores.
"""
