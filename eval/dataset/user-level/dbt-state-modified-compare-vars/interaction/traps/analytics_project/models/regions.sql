{{ config(tags=[var("region_tag")]) }}

select
    1 as region_id,
    '{{ var("region_tag") }}' as region_tag,
    '{{ var("owner_team") }}' as owner_team
