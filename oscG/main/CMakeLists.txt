idf_component_register(SRCS "main.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES daisysp freertos  driver  nvs_flash  esp_netif esp_eth lwip mynet )
