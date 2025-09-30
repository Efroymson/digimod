idf_component_register(SRCS "net.c"
                       INCLUDE_DIRS "."
                       REQUIRES lwip nvs_flash driver
                       PRIV_REQUIRES esp_event esp_eth esp_netif)
