import pandas as pd
import numpy as np
from G2Av1226 import Glicko2, Rating
from datetime import datetime

# Creamos la clase MatchAnalyst para recolectar datos
class MatchAnalyst:
    def __init__(self, df_stadiums, df_economy, df_macro, df_sports=None, df_weather=None,
                 xg_reference=1.35, rest_days_threshold=15):
        # Guardamos los DataFrames y preprocesamos algunos de ellos para facilitar la extracción de datos
        self.df_stadiums = df_stadiums.set_index('Team')
        self.df_economy = df_economy.copy()
        self.df_macro = df_macro
        self.df_sports = self._prepare_sports_df(df_sports)
        self.df_weather = df_weather  # Puede ser None al principio
        self.xg_reference = xg_reference
        self.rest_days_threshold = rest_days_threshold
        # Aseguramos que la columna de Market Value esté presente con el nombre correcto
        # Codigo legado, esto fue corregido posterior a la creacion del .py
        if 'MarketValue' not in self.df_economy.columns and 'MarketValue 2017 Q2' in self.df_economy.columns:
            self.df_economy = self.df_economy.rename(columns={'MarketValue 2017 Q2': 'MarketValue'})
    # Obtencion de metricas dado el partido (fecha, equipo local y equipo visitante)
    def get_metrics(self, date_str, home_team, away_team):
        """
        Orquesta la recolección de datos desde los 3 excels distintos.
        """
        date_obj = pd.to_datetime(date_str, dayfirst=True)
        year = date_obj.year
        month = date_obj.month
        
        # 1. Calcular Semestre (Para EconomyDB)
        # Asumo: Ene-Jun = 1, Jul-Dic = 2
        semester = 1 if month <= 6 else 2
        
        # 2. Calcular "Temporada" string (Para StadiumsDB Assist_XXYY)
        # Si es Ago-2018, es temporada 1819. Si es Ene-2019, es temporada 1819.
        if month >= 8:
            season_str = f"{str(year)[-2:]}{str(year+1)[-2:]}" # Ej: 1819
        else:
            season_str = f"{str(year-1)[-2:]}{str(year)[-2:]}" # Ej: 1718
        
        col_assist = f"Assist_{season_str}"

        # --- EXTRACCIÓN DE DATOS ---

        # Obtenemos datos de Estadio (Altura y Asistencia)
        try:
            home_stadium = self.df_stadiums.loc[home_team]
            altitude_home = home_stadium['Height'] #Altura
            capacity = home_stadium['Capacity'] #Capacidad
            
            # Intenta buscar la columna específica del año, si no, usa promedio
            if col_assist in self.df_stadiums.columns:
                attendance = home_stadium[col_assist]
            # Y en caso de que no haya datos de asistencia
            else:
                attendance = capacity * 0.5 
                
            # calculo de metrica "ratio de ocupacion"
            or_rate = attendance / capacity if capacity > 0 else 0.5
        # Caso error
        except KeyError:
            altitude_home = 0
            or_rate = 0.5

        # Calculo de PO2 (Usando Altura del Estadio)
        # Necesitamos la altura promedio del visitante para comparar
        try:
            altitude_away = self.df_stadiums.loc[away_team, 'Height']
        except KeyError:
            altitude_away = 0 # Asumimos nivel del mar si no hay dato
            
        po_impact = self._calculate_po_impact(altitude_home, altitude_away)

        # Calculamos la diferencia de valor de mercado
        d_mv = self._get_market_diff(home_team, away_team, year, semester)

        # Obtencion de variables climaticas
        # Por ahora devolveremos 0 en diferencias climáticas
        d_hum = 0.0
        d_tem = 0.0
        
        if self.df_weather is not None:
             # Aquí iría la lógica de buscar en df_weather por fecha y estadio
             pass

        # Calculamos xG de manera teorica a traves de calculate rolling
        if self.df_sports is not None:
            xg_home = self._calculate_rolling_xg(home_team, date_obj)
        else:
            xg_home = self.xg_reference

        return {
            'xg': xg_home,
            'or': or_rate,
            'po': po_impact,
            'd_hum': d_hum,
            'd_tem': d_tem,
            'd_mv': d_mv
        }

    # Funcion para calcular la PO2 de forma teorica
    def _calculate_po_impact(self, h_home, h_away):
        # Fórmula ISA simplificada para presión de oxígeno
        def get_po2(alt):
            if pd.isna(alt): return 212.0
            return (1013.25 * (1 - (0.0065 * alt) / 288.15) ** 5.255) * 0.2095
            
        po_home = get_po2(h_home)
        po_away = get_po2(h_away)
        
        # El impacto es positivo si el visitante tiene más oxígeno en casa del que hay hoy
        return max(0, po_away - po_home)

    # Función para actualizar el rating de un equipo después de un partido
    def _prepare_sports_df(self, df_sports):
        if df_sports is None:
            return None

        df = df_sports.copy()

        # Normalize common column variants from concat files
        col_map = {
            'Performance PKatt': 'PKatt',
            'Performance SoT': 'SoT',
            'Performance Sh': 'Sh',
            'Performance PK': 'PK',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], dayfirst=True)
        elif 'match_date' in df.columns:
            df['date'] = pd.to_datetime(df['match_date'], dayfirst=True)

        return df

    # Calculo de xG de forma teorica usando 5 partidos como periodo
    def _calculate_rolling_xg(self, team, date_obj, window=5):
        # Filter previous matches for the team
        mask = (self.df_sports['team'] == team) & (self.df_sports['date'] < date_obj)
        last_games = self.df_sports[mask].sort_values('date', ascending=False).head(window)
        # Caso borde: No hay datos suficiente
        if last_games.empty:
            return self.xg_reference

        # Caso borde. Penaliza inactiviad
        last_date = last_games['date'].iloc[0]
        if (date_obj - last_date).days >= self.rest_days_threshold:
            return self.xg_reference

        # Calculamos el xG promedio de los últimos partidos usando una fórmula ponderada de métricas ofensivas
        def _get_col_avg(df, col_candidates):
            for col in col_candidates:
                if col in df.columns:
                    return df[col].mean()
            return 0.0
        # Calculamos promedios
        avg_pkatt = _get_col_avg(last_games, ['PKatt', 'Pkatt', 'PK'])
        avg_sot = _get_col_avg(last_games, ['SoT'])
        avg_sh = _get_col_avg(last_games, ['Sh'])

        # Formula teorica de xG: PKatt*0.78 + SoT*0.3 + Sh*0.05
        return (0.78 * avg_pkatt) + (0.3 * avg_sot) + (0.05 * avg_sh)

    # Funcion para calculo de diferencia de valor de mercado
    def _get_market_diff(self, home, away, year, semester):
        # Filtramos EconomyDB por año y semestre
        period_data = self.df_economy[
            (self.df_economy['Year'] == year) & 
            (self.df_economy['Semester'] == semester)
        ]
        # Obtenemos el valor de mercado de ambos equipos
        # Si no hay datos, asumimos un valor por defecto (1 millon de euros)
        def get_val(team_name):
            row = period_data[period_data['Team'] == team_name]
            if not row.empty:
                return row.iloc[0]['MarketValue'] 
            return 1.0 # Valor por defecto en millones de euros
            
        v_home = get_val(home)
        v_away = get_val(away)
        
        return v_home - v_away