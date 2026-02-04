import pdfplumber
import json
import os
import re

def limpiar_espacios(texto):
    if not texto: return ""
    return ' '.join(str(texto).split()).strip()

def es_basura_o_legal(texto_fila):
    """Detector de zonas prohibidas"""
    texto = texto_fila.lower()
    
    palabras_prohibidas = [
        "mitigación de riesgos", "seguridad del aplicador", "gestión de envases",
        "primeros auxilios", "almacenamiento", "eliminación del producto",
        "no contaminar", "clase y categoría", "indicaciones de peligro",
        "consejos de prudencia", "zona de protección", "banda de seguridad",
        "conforme a lo establecido", "real decreto", "orden jus",
        "euh401", "spe3", "spe8",
        "plazos de seguridad", "protección del consumidor", 
        "pictograma", "palabra de advertencia", "ghs0",
        "secretaria general", "dirección general", "producción agroalimentaria",
        "gobierno de españa", "ministerio de agricultura"
    ]

    return any(p in texto for p in palabras_prohibidas)

def es_dosis_valida(texto):
    """Verifica que la dosis sea válida"""
    texto = texto.lower().strip()
    if not texto: return False
    
    # Rechazar nombres de cultivos en dosis
    cultivos_falsos = ['tomate', 'pimiento', 'pepino', 'melón', 'sandía', 'ciruelo', 'cerezo']
    if any(c in texto for c in cultivos_falsos):
        return False

    # Debe tener números O ser texto válido
    tiene_numero = bool(re.search(r'\d', texto))
    es_texto_valido = any(x in texto for x in ['consultar', 'etiqueta', 'na', 'n/a'])
    
    return (tiene_numero or es_texto_valido) and len(texto) < 60

def detectar_filas_graficas(pagina):
    """Detecta líneas horizontales"""
    objetos = pagina.lines + pagina.rects
    cortes_y = []
    
    for obj in objetos:
        if obj['width'] > 100: 
            cortes_y.append(obj['top'])
            if obj['height'] > 1 and obj['height'] < 10:
                cortes_y.append(obj['bottom'])

    cortes_y = sorted(list(set([round(y) for y in cortes_y])))
    
    filas_limpias = []
    if cortes_y:
        filas_limpias.append(cortes_y[0])
        for y in cortes_y[1:]:
            if y - filas_limpias[-1] > 8: 
                filas_limpias.append(y)
    return filas_limpias

def extraer_por_lineas_y(pagina, cols):
    """Método alternativo: agrupa por coordenada Y (para PDFs sin líneas)"""
    palabras = pagina.extract_words(x_tolerance=3, y_tolerance=3)
    if not palabras:
        return []
    
    # Agrupar por Y
    lineas_y = {}
    for w in palabras:
        y = round(w['top'] / 3) * 3
        if y not in lineas_y:
            lineas_y[y] = []
        lineas_y[y].append(w)
    
    filas = []
    for y in sorted(lineas_y.keys()):
        palabras_fila = lineas_y[y]
        
        # Asignar a columnas
        texto_cols = {c: [] for c in cols}
        for w in palabras_fila:
            for col_nom, (xmin, xmax) in cols.items():
                if xmin <= w['x0'] < xmax:
                    texto_cols[col_nom].append(w['text'])
                    break
        
        fila = {k: " ".join(v).strip() for k, v in texto_cols.items()}
        filas.append(fila)
    
    return filas

def extraer_tabla_mejorada(ruta_pdf):
    datos = []
    
    cols = {
        'uso': (0, 110),
        'agente': (110, 265),
        'dosis': (265, 350),
        'n_aplic': (350, 405),
        'intervalo': (405, 485),
        'vol_caldo': (485, 600),
        'condiciones': (600, 1000)
    }

    try:
        with pdfplumber.open(ruta_pdf) as pdf:
            uso_actual = ""
            tabla_encontrada = False
            fin_de_tabla = False

            for num_pag, pagina in enumerate(pdf.pages):
                if fin_de_tabla:
                    break
                
                print(f"   📄 Página {num_pag + 1}")
                
                # Intentar primero con líneas gráficas
                cortes = detectar_filas_graficas(pagina)
                
                if len(cortes) < 2:
                    # SIN LÍNEAS: usar método alternativo
                    print(f"      ⚠️  Sin líneas gráficas, usando agrupación por Y")
                    filas_datos = extraer_por_lineas_y(pagina, cols)
                else:
                    # CON LÍNEAS: método original
                    palabras_pag = pagina.extract_words(x_tolerance=2, y_tolerance=2)
                    filas_datos = []
                    
                    for i in range(len(cortes) - 1):
                        y_top = cortes[i]
                        y_bottom = cortes[i+1]
                        
                        palabras_fila = [w for w in palabras_pag 
                                        if y_top <= (w['top'] + (w['bottom']-w['top'])/2) < y_bottom]
                        if not palabras_fila:
                            continue

                        texto_cols = {c: [] for c in cols}
                        for w in palabras_fila:
                            for col_nom, (xmin, xmax) in cols.items():
                                if xmin <= w['x0'] < xmax:
                                    texto_cols[col_nom].append(w['text'])
                                    break
                        
                        fila = {k: " ".join(v).strip() for k, v in texto_cols.items()}
                        filas_datos.append(fila)
                
                # Procesar filas
                for fila in filas_datos:
                    texto_completo = " ".join(fila.values()).lower()

                    # STOP: Fin de tabla
                    if "plazos de seguridad" in texto_completo or "protección del consumidor" in texto_completo:
                        fin_de_tabla = True
                        print(f"      🛑 Fin de tabla detectado")
                        break

                    # Cabecera - más flexible
                    if not tabla_encontrada:
                        if ("uso" in texto_completo or "cultivo" in texto_completo) and \
                           ("agente" in texto_completo or "plaga" in texto_completo):
                            tabla_encontrada = True
                            print(f"      ✅ Cabecera detectada")
                            continue
                        else:
                            continue
                    
                    # Filtros
                    if es_basura_o_legal(texto_completo):
                        continue

                    if not es_dosis_valida(fila['dosis']):
                        if len(fila['dosis']) > 2 and "consultar" not in fila['dosis'].lower():
                            continue

                    # Procesar
                    if len(fila['uso']) > 2:
                        uso_actual = fila['uso']
                    
                    tiene_datos = len(fila['agente']) > 1 or len(fila['dosis']) > 0
                    
                    if tiene_datos and fila['agente']:
                        if "pictograma" in fila['agente'].lower():
                            continue

                        lista_agentes = [fila['agente']]
                        if "," in fila['agente'] and len(fila['agente']) < 50:
                            lista_agentes = [x.strip() for x in fila['agente'].split(',')]

                        for ag in lista_agentes:
                            if not ag:
                                continue
                            
                            datos.append({
                                "uso": uso_actual,
                                "agente": ag,
                                "dosis": fila['dosis'],
                                "n_aplic": fila['n_aplic'],
                                "intervalo": fila['intervalo'],
                                "vol_caldo": fila['vol_caldo'],
                                "condiciones": fila['condiciones'],
                                "archivo": os.path.basename(ruta_pdf)
                            })
                
                if tabla_encontrada and len(datos) > 0:
                    print(f"      ✅ {len([d for d in datos if d['archivo'] == os.path.basename(ruta_pdf)])} registros")

            # Herencia de dosis
            for i in range(len(datos)):
                if datos[i]['agente'] and not datos[i]['dosis']:
                    if i + 1 < len(datos):
                        vecino = datos[i+1]
                        if vecino['uso'] == datos[i]['uso'] and vecino['dosis']:
                            datos[i]['dosis'] = vecino['dosis']
                            datos[i]['n_aplic'] = vecino['n_aplic']
                            datos[i]['intervalo'] = vecino['intervalo']
                            datos[i]['vol_caldo'] = vecino['vol_caldo']
                            if not datos[i]['condiciones']:
                                datos[i]['condiciones'] = vecino['condiciones']

    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()

    # Limpieza final
    datos_finales = [d for d in datos if es_dosis_valida(d['dosis'])]
    return datos_finales

def procesar_carpeta(carpeta):
    todos = []
    if not os.path.exists(carpeta):
        print(f"❌ Carpeta '{carpeta}' no encontrada")
        return
    
    archivos = [f for f in os.listdir(carpeta) if f.endswith('.pdf')]
    
    if not archivos:
        print(f"❌ No hay PDFs en '{carpeta}'")
        return
    
    print(f"\n{'='*70}")
    print(f"🎯 EXTRACCIÓN MEJORADA - MANEJA PDFs CON Y SIN LÍNEAS")
    print(f"{'='*70}\n")
    
    for f in archivos:
        print(f"📄 {f}")
        items = extraer_tabla_mejorada(os.path.join(carpeta, f))
        todos.extend(items)
        print()

    with open('usos_y_dosis_definitivo_v2.json', 'w', encoding='utf-8') as f:
        json.dump(todos, f, indent=4, ensure_ascii=False)
    
    print(f"{'='*70}")
    print(f"✅ COMPLETADO")
    print(f"📊 Total: {len(todos)} registros")
    print(f"💾 Archivo: usos_y_dosis_definitivo.json")
    print(f"{'='*70}\n")
    
    # Mostrar ejemplos
    if todos:
        print("👀 EJEMPLOS:\n")
        for i, item in enumerate(todos[:5], 1):
            print(f"#{i} - {item['archivo']}")
            print(f"  Uso: {item['uso']}")
            print(f"  Agente: {item['agente']}")
            print(f"  Dosis: {item['dosis']}")
            print()

if __name__ == "__main__":
    procesar_carpeta('mis_pdfs')