"""Build the importable statistics and setpoint dashboard from the working one."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT/'deploy'/'grafana'
    d = json.loads((folder/'simulation-import.json').read_text(encoding='utf-8'))
    d['version'] = 6
    d['title'] = 'Air heater control and statistics'
    # Same dashboard UID: importing can replace the existing dashboard.
    for panel in d['panels']:
        panel['fieldConfig']['defaults']['decimals'] = 2
    ds = copy.deepcopy(d['panels'][0]['datasource'])
    d['panels'][2]['gridPos'] = dict(x=0, y=9, w=6, h=5)
    d['panels'][2]['fieldConfig']['defaults']['thresholds']['steps'][1]['value'] = 30
    where = (' FROM public.simulation_measurements WHERE $__timeFilter(measured_at)'
             ' AND device_id IN (${device:sqlstring})')
    specs = [
        (4, 'Mean absolute error',
         'SELECT avg(abs(setpoint_c - temperature_c)) AS "Absolute error"'+where+
         ' AND setpoint_c IS NOT NULL', 'celsius', 6,
         'Sample mean of |setpoint - raw temperature| over the selected period. '
         'Measurements without a setpoint are excluded. This is not the filtered control error.'),
        (5, 'Min. / max. temperature',
         'SELECT min(temperature_c) AS "Minimum", max(temperature_c) AS "Maximum"'+where,
         'celsius', 12, 'Lowest and highest raw temperature for the selected period and devices.'),
        (6, 'Mean heater input',
         'SELECT avg(heater_voltage) AS "Heater input"'+where, 'volt', 18,
         'Sample mean over the selected period. Input voltage, not electrical power or energy.')]
    for pid, title, sql, unit, x, description in specs:
        d['panels'].append(dict(id=pid, title=title, description=description, type='stat',
            datasource=copy.deepcopy(ds), gridPos=dict(x=x, y=9, w=6, h=5),
            fieldConfig=dict(defaults=dict(unit=unit, decimals=2, noValue='No measurements'), overrides=[]),
            options=dict(reduceOptions=dict(values=False, calcs=['lastNotNull'], fields=''),
                         orientation='auto', textMode='value_and_name', colorMode='value', graphMode='none'),
            targets=[dict(refId='A', format='table', rawQuery=True, rawSql=sql)]))
    def element(uid, eid, title, kind, value, **extra):
        return dict(uid=uid, id=eid, title=title, type=kind, value=value, width=None,
                    labelWidth=18, tooltip='', section='', hidden=False, unit='', **extra)
    panel = dict(id=7, type='volkovlabs-form-panel', title='Change Pi setpoint',
        description='Controls airheater-sim-1 without an operator key. '
                    'Confirmation comes from the Pi. The chart updates with the next telemetry message.',
        gridPos=dict(x=0, y=14, w=24, h=9), datasource=copy.deepcopy(ds), targets=[],
        options=dict(sync=False, updateEnabled='manual',
            layout=dict(variant='single', orientation='vertical', padding=12, sectionVariant='default', sections=[]),
            elements=[element('setpoint-field', 'setpoint_c', 'New setpoint [°C]', 'number', 34,
                              min=25, max=36, step=.01),
                      element('command-status', 'status', 'Status', 'disabled', 'No command sent')],
            initial=dict(method='-', code='context.panel.enableSubmit();', url='', datasource='', header=[]),
            update=dict(method='-', code=(folder/'setpoint-submit.js').read_text(encoding='utf-8'),
                        confirm=False, url='', datasource='', header=[]),
            resetAction=dict(mode='custom', code='', confirm=False),
            submit=dict(variant='primary', text='Send setpoint', icon='cloud-upload',
                        foregroundColor='', backgroundColor=''),
            reset=dict(variant='hidden', text='Reset', icon='process'),
            saveDefault=dict(variant='hidden', text='Save Default', icon='save'),
            buttonGroup=dict(orientation='left', size='md'),
            elementValueChanged='context.panel.enableSubmit();'))
    d['panels'].append(panel)
    d['templating']['list'].append(dict(name='control_api_url', label='Control API URL', type='constant',
        hide=2, query='https://iotlab1.swedencentral.cloudapp.azure.com',
        current=dict(text='https://iotlab1.swedencentral.cloudapp.azure.com',
                     value='https://iotlab1.swedencentral.cloudapp.azure.com')))
    d['__requires'] = [dict(type='panel', id='volkovlabs-form-panel', name='Business Forms', version='4.4.0')]
    result = folder/'simulation-control-import.json'
    result.write_text(json.dumps(d, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(result)


if __name__ == '__main__':
    main()
