idf_component_register(SRCS "main.cpp"
                       INCLUDE_DIRS "."
                       REQUIRES daisysp driver  nvs_flash  esp_netif esp_eth lwip pot_controller)
