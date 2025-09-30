idf_component_register(SRCS "ui.c"
                       INCLUDE_DIRS "."
		       REQUIRES esp_adc
                       PRIV_REQUIRES esp_timer esp_driver_gpio driver nvs_flash)
