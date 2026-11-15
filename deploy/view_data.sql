SELECT device_id, sequence_number, measured_at,
       temperature_c, setpoint_c, heater_voltage, enqueued_at, received_at
FROM public.simulation_measurements
ORDER BY received_at DESC
LIMIT 65;
